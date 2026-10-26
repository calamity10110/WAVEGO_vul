// GaitEngine — parametric gait planner: mode (crawl/walk/run), continuous
// speed (stride x cadence), body height easing, and distance targeting.
//
// PURE MODULE: no Arduino, no Serial, no servo calls — compiles host-side
// (g++ unit tests in wavego_pro_platformio/tests/). The BodyCtrl wrapper
// applies each Tick to the existing IK path (simpleGait/triangularGait,
// WALK_HEIGHT/WALK_RANGE/WALK_LIFT_PROP live variables, allJointAngle).
//
// Geometry facts this module relies on (BodyCtrl.cpp):
//   - one gait cycle = 1.0 of phase; the firmware sweeps
//     rDist over (stride*status + 2*WALK_ACC) mm per stance, so distance
//     traveled per full cycle = strideEff + 2*acc.
//   - WALK_LIFT_PROP is the swing fraction (1 - duty); it is consumed
//     live by singleGaitCtrl, safe to set per mode. Never feed the
//     boot-time pre-rendered constants (O_WLP, WALK_H_L) runtime values.
#ifndef GAIT_ENGINE_H
#define GAIT_ENGINE_H

#include <stdint.h>

namespace gait {

enum Mode : uint8_t { CRAWL = 0, WALK = 1, RUN = 2 };

struct ModeParams {
    float    swingProp;     // fraction of the cycle in swing (1 - duty)
    bool     creepPlan;     // true: 4 legs 0.25 apart (one swing at a time);
                            // false: diagonal pairs 0.5 apart
    bool     comShift;      // weight-shift toward the stance triangle (crawl)
    float    strideLoMm;    // stride at speed=0
    float    strideHiMm;    // stride at speed=1 (RUN shortens with speed)
    int      delayLoMs;     // per-tick delay at speed=0
    int      delayHiMs;     // per-tick delay at speed=1
    float    iterateLo;     // phase advance per tick at speed=0
    float    iterateHi;     // phase advance per tick at speed=1
    float    speedCeil;     // safety clamp (<1.0 for modes not yet verified)
};

// Full engine input. Every field optional at the protocol layer: the
// caller merges onto the live state before configure().
struct Params {
    Mode     mode;
    float    speed;         // 0..1, clamped by the mode's speedCeil
    float    headingDeg;    // -180..180, 0 = forward, + = left
    float    heightMm;      // stance height target (clamped to bounds-lift)
    float    strideMm;      // 0 = mode default for current speed
    float    distanceMm;    // 0 = until stopped; >0 = auto-stop after target
};

// One planner output per firmware loop tick.
struct Tick {
    float    phase;         // 0..1 global cycle phase to feed gaitTypeCtrl
    float    headingDeg;
    float    strideScale;   // statusInput multiplier (1.0 in v1)
    float    heightNowMm;   // eased live WALK_HEIGHT to write
    int      turnCmd;       // 0 in v1 (heading covers arcs; T:111 turns stay)
    bool     done;          // distance target just completed (fires once)
};

class Engine {
public:
    Engine();

    // Bounds mirror the firmware's live WALK_* limits (settable via T:116).
    void  setBounds(float heightMinMm, float heightMaxMm, float accMm, float liftMm);

    // Merge params onto current state and clamp. Activates motion when
    // speed > 0 (or when already active — live update).
    void  configure(const Params& p);

    // Planner step. Only valid while active(); advances phase, integrates
    // distance at cycle boundaries, eases height toward target.
    Tick  tick();

    void  stop();           // deactivate (caller stands the robot)
    bool  active() const { return active_; }

    // Introspection for the T:-117 feedback event.
    float    traveledMm() const { return traveledMm_; }
    uint32_t cycles()     const { return cycles_; }
    Mode     mode()       const { return mode_; }
    float    speed()      const { return speed_; }

    // Effective stride (mm) at the current mode/speed — also what the
    // wrapper writes into WALK_RANGE.
    float strideEffMm() const;

    // Effective per-tick cadence at the current mode/speed — what the
    // wrapper writes into STEP_DELAY / STEP_ITERATE.
    int   delayEffMs() const;
    float iterateEff() const;

    static const ModeParams& modeParams(Mode m);

private:
    float clampSpeed_(float s) const;
    float clampHeight_(float h) const;
    float lerp_(float a, float b, float t) const { return a + (b - a) * t; }

    bool     active_ = false;
    Mode     mode_   = WALK;
    float    speed_  = 0.0f;
    float    headingDeg_ = 0.0f;
    float    heightTargetMm_ = 95.0f;
    float    strideMm_ = 0.0f;     // 0 = mode default
    float    distanceTargetMm_ = 0.0f;
    float    heightMinMm_ = 75.0f, heightMaxMm_ = 110.0f;
    float    accMm_ = 5.0f, liftMm_ = 9.0f;

    float    phase_ = 0.0f;
    uint32_t ticks_ = 0;
    float    heightNowMm_ = 95.0f;
    float    traveledMm_ = 0.0f;
    uint32_t cycles_ = 0;
    bool     doneLatched_ = false;
};

} // namespace gait

#endif // GAIT_ENGINE_H
