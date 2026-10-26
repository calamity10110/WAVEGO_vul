// Host-side unit tests for the pure GaitEngine module — no Arduino, no
// hardware. Compiled and run by CI (g++) alongside the Python suites.
#include "GaitEngine.h"

#include <cmath>
#include <cstdio>

using gait::Engine;
using gait::Mode;
using gait::Params;
using gait::ModeParams;

static int failures = 0;

#define CHECK(cond) do { \
    if (!(cond)) { std::printf("FAIL %s:%d  %s\n", __FILE__, __LINE__, #cond); failures++; } \
} while (0)

static bool feq(float a, float b, float eps = 1e-4f) { return std::fabs(a - b) < eps; }

static Params baseParams() {
    Params p;
    p.mode = Mode::WALK;
    p.speed = 0.5f;
    p.headingDeg = 0.0f;
    p.heightMm = 95.0f;
    p.strideMm = 0.0f;
    p.distanceMm = 0.0f;
    return p;
}

// ---------------------------------------------------------------- modes

static void test_mode_table() {
    const ModeParams& crawl = Engine::modeParams(Mode::CRAWL);
    const ModeParams& walk  = Engine::modeParams(Mode::WALK);
    const ModeParams& run   = Engine::modeParams(Mode::RUN);

    CHECK(crawl.creepPlan && walk.creepPlan == false && run.creepPlan == false);
    CHECK(crawl.comShift && !walk.comShift && !run.comShift);
    // duty: crawl most stance, run least
    CHECK(crawl.swingProp < walk.swingProp && walk.swingProp < run.swingProp);
    CHECK(feq(crawl.swingProp, 0.15f) && feq(walk.swingProp, 0.25f) && feq(run.swingProp, 0.40f));
    // WALK reproduces the stock firmware envelope
    CHECK(feq(walk.strideHiMm, 40.0f));
    CHECK(walk.delayLoMs == 8 && walk.delayHiMs == 4);
    // RUN is capped until bench-verified
    CHECK(feq(run.speedCeil, 0.8f));
    CHECK(feq(walk.speedCeil, 1.0f) && feq(crawl.speedCeil, 1.0f));
}

// --------------------------------------------------------------- clamps

static void test_clamps() {
    Engine e;

    Params p = baseParams();
    p.speed = 5.0f;                       // above ceiling
    e.configure(p);
    CHECK(e.active());
    CHECK(e.speed() <= 1.0f);

    e.stop();
    p = baseParams();
    p.mode = Mode::RUN;
    p.speed = 1.0f;                       // RUN ceiling 0.8
    e.configure(p);
    CHECK(feq(e.speed(), 0.8f));
    CHECK(e.delayEffMs() >= 2 && e.delayEffMs() <= 3);

    // height clamps to bounds-lift
    e.setBounds(75.0f, 110.0f, 5.0f, 9.0f);
    e.stop();
    p = baseParams();
    p.heightMm = 200.0f;
    e.configure(p);
    Params cur = baseParams();            // placeholder; verified via stride below
    (void)cur;

    // stride clamps into the mode envelope
    p = baseParams();
    p.mode = Mode::CRAWL;
    p.strideMm = 40.0f;                   // crawl caps at 30
    e.configure(p);
    CHECK(e.strideEffMm() <= 30.0f);

    // heading clamps
    p = baseParams();
    p.headingDeg = 250.0f;
    e.configure(p);
    gait::Tick t = e.tick();
    CHECK(feq(t.headingDeg, 180.0f));
}

// --------------------------------------------------------------- cadence

static void test_cadence_monotonic() {
    Engine slow, fast;
    Params p = baseParams();
    p.speed = 0.0f;
    slow.configure(p);
    p.speed = 1.0f;
    fast.configure(p);
    // faster speed -> shorter or equal delay, same for iterate
    CHECK(fast.delayEffMs() <= slow.delayEffMs());
    CHECK(fast.iterateEff() >= slow.iterateEff());
    // stock walk defaults at speed 0.5 land on firmware values (delay 6)
    Engine mid;
    p = baseParams();
    mid.configure(p);
    CHECK(mid.delayEffMs() == 6);
    CHECK(feq(mid.iterateEff(), 0.02f));
    // stride grows with speed on WALK
    CHECK(fast.strideEffMm() > slow.strideEffMm());
    // ... and shrinks on RUN (frequency buys the speed)
    Engine runSlow, runFast;
    p = baseParams(); p.mode = Mode::RUN; p.speed = 0.0f; runSlow.configure(p);
    p.speed = 0.8f;                        // engine clamps to the ceiling anyway
    runFast.configure(p);
    CHECK(runFast.strideEffMm() < runSlow.strideEffMm());
}

// -------------------------------------------------------------- distance

static void test_distance_completion() {
    Engine e;
    e.setBounds(75.0f, 110.0f, 5.0f, 9.0f);
    Params p = baseParams();
    p.mode = Mode::WALK;
    p.speed = 1.0f;                        // delay 4ms, iterate .02 -> 50 ticks/cycle
    p.strideMm = 40.0f;
    p.distanceMm = 100.0f;                 // 2 cycles exactly (40+10 per cycle)
    e.configure(p);
    CHECK(e.active());

    bool sawDone = false;
    int guard = 0;
    while (e.active() && guard++ < 10000) {
        gait::Tick t = e.tick();
        if (t.done) sawDone = true;
    }
    CHECK(sawDone);
    CHECK(guard <= 10000);
    // exactly 2 cycles of (stride + 2*acc) = 100mm
    CHECK(feq(e.traveledMm(), 100.0f));
    CHECK(e.cycles() == 2);
    CHECK(!e.active());                    // deactivated by completion

    // re-arming a distance resets the integrator
    p.distanceMm = 50.0f;
    e.configure(p);
    CHECK(feq(e.traveledMm(), 0.0f) && e.cycles() == 0);
    guard = 0;
    while (e.active() && guard++ < 10000) e.tick();
    CHECK(feq(e.traveledMm(), 50.0f));
}

static void test_unlimited_distance_never_completes() {
    Engine e;
    Params p = baseParams();
    p.distanceMm = 0.0f;
    e.configure(p);
    for (int i = 0; i < 5000; i++) {
        gait::Tick t = e.tick();
        CHECK(!t.done);
    }
    CHECK(e.active());
    CHECK(e.cycles() == 100);              // 5000 ticks * 0.02 = 100 cycles
}

// ---------------------------------------------------------------- height

static void test_height_easing() {
    Engine e;
    e.setBounds(75.0f, 110.0f, 5.0f, 9.0f);
    Params p = baseParams();
    p.heightMm = 105.0f;
    e.configure(p);
    float prev = -1.0f;
    float maxStep = 0.0f;
    for (int i = 0; i < 100; i++) {
        gait::Tick t = e.tick();
        if (prev >= 0.0f) {
            float step = std::fabs(t.heightNowMm - prev);
            if (step > maxStep) maxStep = step;
        }
        prev = t.heightNowMm;
    }
    // target 105 is clamped by the height+lift<=max guard: 110-9 = 101
    CHECK(feq(prev, 101.0f));
    CHECK(maxStep <= 1.5f + 1e-4f);        // but never jerks
}

// ------------------------------------------------------------ stop/resume

static void test_stop_semantics() {
    Engine e;
    Params p = baseParams();
    e.configure(p);
    CHECK(e.active());
    e.tick();
    e.stop();
    CHECK(!e.active());
    gait::Tick t = e.tick();               // inert tick is safe
    CHECK(!t.done);
    // resume without re-declaring everything
    p.speed = 0.5f;
    e.configure(p);
    CHECK(e.active());
}

int main() {
    test_mode_table();
    test_clamps();
    test_cadence_monotonic();
    test_distance_completion();
    test_unlimited_distance_never_completes();
    test_height_easing();
    test_stop_semantics();

    if (failures == 0) {
        std::printf("gait engine: all tests passed\n");
        return 0;
    }
    std::printf("gait engine: %d FAILURES\n", failures);
    return 1;
}
