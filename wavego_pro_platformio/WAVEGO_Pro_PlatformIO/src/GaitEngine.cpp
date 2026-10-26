#include "GaitEngine.h"

namespace gait {

// Mode table — the gait engineering core. Values for WALK reproduce the
// stock firmware (duty 75%, stride 40mm, delay 4-8ms, iterate 0.02) so
// mode 1 is the verified anchor; CRAWL/RUN are new and need the bench
// session from the docs (RUN ships behind speedCeil 0.8 until then).
//
//  CRAWL: one leg swings at a time (creep), weight shifted off it —
//         statically stable at any speed, slow.
//  WALK:  stock diagonal pairs.
//  RUN:   diagonal pairs, shorter swing (60% stance) and shorter stride
//         at top speed so the legs can still close the cycle. No aerial
//         phase: 2.3 kg.cm servos, open loop, no gyro — fast-trot ceiling.
static const ModeParams MODES[3] = {
    // swingProp creep  comShift strideLo strideHi delayLo delayHi iterLo iterHi ceil
    { 0.15f, true,  true,  12.0f, 30.0f, 14, 9, 0.02f, 0.02f, 1.0f }, // CRAWL
    { 0.25f, false, false, 16.0f, 40.0f,  8, 4, 0.02f, 0.02f, 1.0f }, // WALK
    { 0.40f, false, false, 32.0f, 24.0f,  3, 2, 0.02f, 0.03f, 0.8f }, // RUN
};

Engine::Engine() {
    last_.mode = WALK;
    last_.speed = 0.5f;
    last_.headingDeg = 0.0f;
    last_.heightMm = 95.0f;
    last_.strideMm = 0.0f;
    last_.distanceMm = 0.0f;
}

const ModeParams& Engine::modeParams(Mode m) {
    return MODES[(m == CRAWL || m == RUN) ? m : WALK];
}

void Engine::setBounds(float heightMinMm, float heightMaxMm, float accMm, float liftMm) {
    heightMinMm_ = heightMinMm;
    heightMaxMm_ = heightMaxMm;
    accMm_       = accMm;
    liftMm_      = liftMm;
    heightTargetMm_ = clampHeight_(heightTargetMm_);
    heightNowMm_    = clampHeight_(heightNowMm_);
}

float Engine::clampSpeed_(float s) const {
    if (s < 0.0f) s = 0.0f;
    float ceilV = modeParams(mode_).speedCeil;
    return (s > ceilV) ? ceilV : s;
}

float Engine::clampHeight_(float h) const {
    if (h < heightMinMm_) h = heightMinMm_;
    if (h > heightMaxMm_) h = heightMaxMm_;
    // swing apex must stay inside the firmware's height envelope
    if (h + liftMm_ > heightMaxMm_) h = heightMaxMm_ - liftMm_;
    return h;
}

float Engine::strideEffMm() const {
    const ModeParams& mp = modeParams(mode_);
    float s = (speed_ <= 0.0f) ? 0.0f : (speed_ >= 1.0f ? 1.0f : speed_);
    // strideMm_ == 0 means "mode default for the current speed".
    // CRAWL/WALK lengthen with speed; RUN shortens (frequency buys speed).
    float def = lerp_(mp.strideLoMm, mp.strideHiMm, s);
    float v = (strideMm_ > 0.0f) ? strideMm_ : def;
    float lo = (mp.strideLoMm < mp.strideHiMm) ? mp.strideLoMm : mp.strideHiMm;
    float hi = (mp.strideLoMm < mp.strideHiMm) ? mp.strideHiMm : mp.strideLoMm;
    if (v < lo) v = lo;
    if (v > hi) v = hi;
    return v;
}

int Engine::delayEffMs() const {
    const ModeParams& mp = modeParams(mode_);
    float s = (speed_ <= 0.0f) ? 0.0f : (speed_ >= 1.0f ? 1.0f : speed_);
    float d = lerp_((float)mp.delayLoMs, (float)mp.delayHiMs, s);
    int ms = (int)(d + 0.5f);
    if (ms < mp.delayHiMs) ms = mp.delayHiMs;   // never faster than the cap
    if (ms > mp.delayLoMs) ms = mp.delayLoMs;
    return ms;
}

float Engine::iterateEff() const {
    const ModeParams& mp = modeParams(mode_);
    float s = (speed_ <= 0.0f) ? 0.0f : (speed_ >= 1.0f ? 1.0f : speed_);
    return lerp_(mp.iterateLo, mp.iterateHi, s);
}

void Engine::configure(const Params& p) {
    mode_ = (p.mode == CRAWL || p.mode == RUN) ? p.mode : WALK;
    speed_         = clampSpeed_(p.speed);
    headingDeg_    = p.headingDeg;
    if (headingDeg_ > 180.0f)  headingDeg_ = 180.0f;
    if (headingDeg_ < -180.0f) headingDeg_ = -180.0f;
    heightTargetMm_ = clampHeight_(p.heightMm);
    strideMm_        = (p.strideMm > 0.0f) ? p.strideMm : 0.0f;
    distanceTargetMm_ = (p.distanceMm > 0.0f) ? p.distanceMm : 0.0f;
    // A distance target (re)armed resets the integrator.
    if (p.distanceMm > 0.0f) {
        traveledMm_ = 0.0f;
        cycles_     = 0;
        doneLatched_ = false;
    }
    if (speed_ > 0.0f) active_ = true;

    // publish the merged state for the next partial configure()
    last_.mode = mode_;
    last_.speed = speed_;
    last_.headingDeg = headingDeg_;
    last_.heightMm = heightTargetMm_;
    last_.strideMm = strideMm_;
    last_.distanceMm = distanceTargetMm_;
}

void Engine::syncHeightNow(float mm) {
    heightNowMm_ = clampHeight_(mm);
}

Tick Engine::tick() {
    Tick t;
    t.phase = phase_;
    t.headingDeg = headingDeg_;
    t.strideScale = 1.0f;
    t.turnCmd = 0;
    t.done = false;
    if (!active_) {
        t.heightNowMm = heightNowMm_;
        return t;
    }

    // height easing: bounded step toward target, no jerk on live WALK_HEIGHT
    const float HEIGHT_STEP_MM = 1.5f;
    if (heightNowMm_ < heightTargetMm_) {
        heightNowMm_ += HEIGHT_STEP_MM;
        if (heightNowMm_ > heightTargetMm_) heightNowMm_ = heightTargetMm_;
    } else if (heightNowMm_ > heightTargetMm_) {
        heightNowMm_ -= HEIGHT_STEP_MM;
        if (heightNowMm_ < heightTargetMm_) heightNowMm_ = heightTargetMm_;
    }
    t.heightNowMm = heightNowMm_;

    // Phase from an exact tick count — accumulation drifts (5000 x 0.02f
    // never lands on an integer), and distance completion must not depend
    // on float luck. The relative epsilon snaps the product onto integer
    // cycle boundaries (float 0.02 is 4.5e-10 low; over 10^6 ticks the
    // shortfall stays far below whole*1e-6 and never bridges a boundary).
    ticks_++;
    double whole = (double)ticks_ * (double)iterateEff();
    uint32_t cycleIndex = (uint32_t)(whole * (1.0 + 1e-6));
    phase_ = (float)(whole - (double)cycleIndex);
    if (cycleIndex > cycles_) {
        cycles_ = cycleIndex;
        traveledMm_ += strideEffMm() + 2.0f * accMm_;
        if (distanceTargetMm_ > 0.0f && !doneLatched_ &&
            traveledMm_ >= distanceTargetMm_) {
            doneLatched_ = true;
            t.done = true;
            active_ = false;   // caller stands the robot on seeing done
        }
    }
    t.phase = phase_;
    return t;
}

void Engine::stop() {
    active_ = false;
    // keep configured params; a later T:117 with speed>0 resumes them
}

} // namespace gait
