# Series Resistance — Debug Notes

Investigation into why the Series Resistance test returns ~2.0–2.1 Ω on every
pin / every matrix configuration, with variation only in the 2nd–3rd digit.

Files involved:
- `core/series_resistance_engine.py` — `SeriesResistanceRunner._run_row`
- `core/engine.py` — base `SweepRunner` (`_apply_voltage`, `_measure`, `_setup_smu`)
- `settings/series_resistance.json` — SMU config
- `keithley4200.py` — driver (`apply()` dispatch on `self.source`)

---

## TL;DR

**The measurement chain looks healthy.** The matrix is switching, the cathode is
force-sourcing current, and the measured voltage tracks the current change. The
constant ~2 Ω is the genuine output of the formula, not an artifact of a broken
matrix or an open circuit. The two things that *looked* like bugs are not:

1. `_apply_voltage` being used for a `"Current in A"` channel — **not a bug.** The
   driver dispatches on sweep mode, so it force-sources current.
2. The `+` sign in the Rs formula — **not a bug.** It is correct *precisely
   because* the sweep currents (and the resulting voltages) are negative.

The real caveats are about **interpretation and robustness**, not correctness
(see "Open risks" below).

---

## 1. `_apply_voltage` is a misnomer, not a dispatch bug

`series_resistance_engine.py` calls `self._apply_voltage(primary, step.voltage)`
for the current-sourced cathode. That is fine:

```python
# core/engine.py
def _apply_voltage(self, ch, voltage):
    smu = self.smu_drivers[ch.role]
    smu.value = voltage
    smu.apply()
```

```python
# keithley4200.py
def apply(self):
    if self.source == "Voltage in V":
        self.set_voltage(...)   # DV
    elif self.source == "Current in A":
        self.set_current(...)   # DI  <-- taken for cathode_SMU1
```

`self.source` comes from `SweepMode` → `ch.sweep_mode` → JSON `"sweep_mode"`.
For `cathode_SMU1` that is `"Current in A"`, so `apply()` emits a `DI` command
and **forces current**. `SweepStep.voltage` is just a generically-named numeric
field; here it carries amps (−0.02, −0.035). Confirmed by the live output:

```
Applied Cathode SMU1 = -0.020 A
Measured Cathode SMU1: -0.611389 V, -0.0199978 A
```

The forced current comes back as the measured current (−0.0199978 ≈ −0.020 A),
and a real junction voltage develops (−0.611 V). Everything is working.

> Naming trap only: `_apply_voltage`, `SweepStep.voltage`, and the
> `"hold at … A"` vs `"… V"` prints are misleading but functionally correct.

---

## 2. Difference vs Dark Current (which works)

| | Dark Current | Series Resistance |
|---|---|---|
| Primary `sweep_mode` | absent → `"Voltage in V"` | `"Current in A"` |
| Primary action | force V, measure I | force I, measure V |
| Primary `compliance_A` | 0.15 (current limit) | 4.0 → **voltage** compliance under `DI` |
| Primary `range` | `"Limited 1 nA"` (`RG`) | `"Auto"` (no range cmd; `DI` range arg = 0) |

When current-sourcing, the JSON `compliance_A: 4.0` is passed as the 4th `DI`
argument = **voltage compliance (4 V)**, not a current limit. Measured V is
~0.6 V, well under 4 V, so we are *not* railing into compliance. Good.

---

## 3. The formula, with the real numbers

```python
rs = ((v2 - v1) + 2.0 * math.log(i2 / i1) * 26e-3) / (i2 - i1)
```

This is the diode model `V = n·Vt·ln(I/Is) + I·Rs` with `n = 2`, `Vt = 26 mV`,
solved for `Rs` across two operating points.

Row 1 numbers:

```
v1 = -0.611389 V   i1 = -0.0199978 A
v2 = -0.671042 V   i2 = -0.0349996 A

Δv          = v2 - v1      = -0.059653 V
Δi          = i2 - i1      = -0.0150018 A
ln(i2/i1)   = ln(1.75018)  =  0.559725
thermal     = 2·0.026·ln   =  0.029106 V

num = Δv + thermal = -0.059653 + 0.029106 = -0.030547
rs  = num / Δi     = -0.030547 / -0.0150018 = 2.036 Ω   ✓ (matches printed 2.0363)
```

### Why the `+` is correct (your I1/I2 question)

The textbook form *subtracts* the thermal term:

```
Rs = [ (V2-V1) - n·Vt·ln(I2/I1) ] / (I2 - I1)
```

That assumes forward bias with **positive** I and V. Here the cathode sources
**negative** current, so V is negative too. Define magnitudes
`V' = -V`, `I' = -I` (both positive). Then:

```
ln(i2/i1)  = ln(I2'/I1')          (the minus signs cancel)
(v2 - v1)  = -(V2' - V1')
(i2 - i1)  = -(I2' - I1')

code = [ (v2-v1) + n·Vt·ln(i2/i1) ] / (i2-i1)
     = [ -(V2'-V1') + n·Vt·ln(I2'/I1') ] / [ -(I2'-I1') ]
     = [  (V2'-V1') - n·Vt·ln(I2'/I1') ] / [  (I2'-I1') ]   ← textbook form
```

So the `+` in the code is the `-` of the standard formula, after the two
negatives in numerator and denominator cancel. **The negative sweep currents
are exactly why the formula reads `+`.**

⚠️ This makes the formula **convention-locked**: it is only correct while I1, I2
are negative. If anyone changes the sweep to positive currents, the `+` becomes
wrong and must flip to `-`. (Sanity check: forcing `-` here would yield 5.92 Ω,
which is the *incorrect* value for this negative-current setup.)

---

## 4. Why it's the same ~2 Ω on every pin (not necessarily a bug)

Two reasons, both expected:

1. **The DUTs are near-identical diodes.** Their forward I–V curves are highly
   reproducible, so V1, V2 barely change pin-to-pin. The matrix *is*
   distinguishing pins — Row 1 = 2.0363 Ω, Row 2 = 2.0402 Ω — the 2nd/3rd-digit
   variation is real device-to-device spread, not noise floor.

2. **The result is dominated by the model constants, not the ohmic drop.**
   `Rs` is the *small difference* of two comparable quantities:
   `Δv = -0.0597 V` vs `thermal = -0.0291 V`. The numerator (−0.0305) is heavily
   shaped by the hardcoded `n = 2` and `Vt = 26 mV`. Change `n` to 1 and the same
   data gives ~3.0 Ω instead of 2.0 Ω. So the measurement is only weakly
   sensitive to the device and strongly sensitive to the assumed diode model —
   which is *why* you see a near-constant number.

---

## 5. Cross-validation: force-V (Dark Current) reproduces the force-I points

To check that the current source, matrix path, and voltage measurement are all
self-consistent, we ran `settings/dark_current_series.json` — same pins, same
matrix configs — but **voltage-driven** at the voltages Rs measured
(−0.611 V, −0.670 V), and measured the resulting current.

```
python sweep.py .\settings\dark_current_series.json --limit-rows 2
```

### 5a. Reciprocity holds (cathode)

| Forced V | Measured I (DC test) | Force-I reference (Rs test) |
|---|---|---|
| −0.611 V | −0.0198868 A (Row 1) / −0.0197798 A (Row 2) | −0.0199978 A @ −0.611389 V |
| −0.670 V | −0.0346819 A (Row 1) / −0.0345338 A (Row 2) | −0.0349996 A @ −0.671042 V |

Force-V and force-I land on the **same I–V point**, confirming the chain is
self-consistent. The small ~0.5 % shortfall (−19.9 mA vs −20.0 mA) is fully
explained by the voltage difference: the DC run was programmed at −0.611 V but
the Rs run actually sat at −0.611389 V (≈0.36 mV more forward bias). With the
diode slope `dI/dV = I/(n·Vt) ≈ 0.02/0.052 ≈ 0.385 A/V`, that 0.36 mV predicts
≈0.14 mA less current → ≈−19.86 mA, matching the measured −19.89 mA. The methods
agree to within the programmed-voltage rounding. ✔

### 5b. NEW finding: the "fixed" 0 V electrodes carry milliamps (return path)

The Dark Current run measures **all** channels (the Rs engine only measures the
primary), which exposes something the Rs test hid. Anode/Guard/Group are held at
0 V yet each sources several mA, and they **sum to the cathode current (KCL):**

Row 1, step 0 (cathode = −19.887 mA):

```
Anode 2.40927 + Guard 5.96859 + Group 11.5131  = 19.891 mA   ≈ |cathode| 19.887 mA  ✔
```

Row 1, step 1 (cathode = −34.682 mA):

```
Anode 3.77478 + Guard 12.4065 + Group 18.5056  = 34.687 mA   ≈ |cathode| 34.682 mA  ✔
```

Row 2 sums match too (19.784 vs 19.780 mA; 34.539 vs 34.534 mA), and the split
differs by pin (e.g. Anode 2.41 mA on OUT1 vs 1.36 mA on IN2), confirming the
matrix really does route differently per pin.

**Implication for Rs.** The cathode current does not return through a single
2-terminal path — it splits across three grounded electrodes
(anode ‖ guard ‖ group). So the voltage the Rs test measures at the cathode is
across the composite `cathode → (anode ‖ guard ‖ group)` network, and the
extracted ~2 Ω is the series resistance of *that whole network*, not a clean
two-terminal device Rs. This is almost certainly the bigger reason the value
looks "the same everywhere": it's dominated by shared, pin-independent return
paths plus the model constants from §4, not by the individual DUT.

> Note: `range: "Limited 1 nA"` on the cathode/anode did not clamp these mA-level
> currents — limited range only sets the autorange floor, and compliance is
> 0.15 A, so autoranging up to tens of mA works fine. (It is slow, though.)

---

## Open risks / recommendations (no code changed yet)

- **Convention-locked sign.** Document, or make robust, the assumption that
  I1, I2 < 0. A future "make currents positive" change silently breaks Rs.
- **Hardcoded `n = 2`, `Vt = 26 mV`.** No temperature compensation, and `n`
  directly sets the answer. Consider making them explicit config, and/or
  deriving `Vt` from temperature.
- **Two-point extraction.** With only two current points you cannot validate
  linearity or separate `Rs` from ideality. A multi-point current sweep + linear
  fit of `dV/dI` (or a proper Rs-extraction at higher currents where `I·Rs`
  dominates the log term) would be far more trustworthy.
- **Sensitivity.** Because `Rs` is a difference of two similar terms divided by a
  small `Δi`, a ~1% error in ΔV or in the assumed `n·Vt` moves `Rs` by a few
  percent. The current 20/35 mA spacing keeps the log term comparable to the
  ohmic term; pushing to higher currents (or wider spacing) improves the SNR of
  the ohmic part.

- **Multi-terminal return path (from §5b).** The cathode current returns through
  anode ‖ guard ‖ group, not a single electrode. The Rs engine only measures the
  primary, so it cannot see this. If a true 2-terminal device Rs is wanted, the
  measurement topology (which electrode is the return, what the guard does) needs
  to be defined deliberately — otherwise ~2 Ω reflects the shared network.

## Verdict

No bug found in the SMU dispatch, the matrix path, or the formula sign. The
force-V cross-check (§5) confirms the current source, matrix routing, and
measurement are all self-consistent and reciprocal. The ~2 Ω is a real result,
but it is dominated by (a) the assumed diode model constants (`n`, `Vt`, §4) and
(b) a multi-electrode return path (§5b) — not by per-device series resistance.
If a true per-device Rs is the goal, the fix is in **methodology and topology**
(multi-point fit, explicit/temperature-aware diode constants, a defined
two-terminal return path), not in correcting a code defect.

---

## 6. Why the result is constant (detailed)

### 6.1 Reframe: Rs = (secant resistance) − (diode's own dynamic resistance)

The formula

```
Rs = [ (V2−V1) − n·Vt·ln(I2/I1) ] / (I2−I1)
```

is algebraically just **two resistances subtracted**:

```
Rs  =   ΔV/ΔI        −    n·Vt·ln(I2/I1)/(I2−I1)
        ▲                  ▲
    total secant       the diode's *own* average
    resistance you     differential resistance over
    measured           the interval  ( = n·Vt / Ī )
```

The second term is **exactly** `n·Vt / Ī`, where `Ī` is the logarithmic-mean
current `(I2−I1)/ln(I2/I1)` — the slope a *pure ideal diode* would show between
those two currents. So the method is: *measure total dV/dI, subtract the part an
ideal diode would contribute, call the remainder Rs.*

### 6.2 Quantitatively (20 → 35 mA)

```
ΔV          = 671.0 − 611.4  = 59.65 mV
ΔI          = 35.0 − 20.0     = 15.0  mA

secant dV/dI      = 59.65 mV / 15.0 mA           = 3.98 Ω   ← total
diode dynamic     = n·Vt/Ī = 0.052 / 26.8 mA     = 1.94 Ω   ← subtracted
                    (Ī = 15/ln(1.75) = 26.8 mA)
─────────────────────────────────────────────────────────
Rs                = 3.98 − 1.94                   = 2.04 Ω
```

### 6.3 Diagram — the network (what's physically there)

The topology the Dark Current force-V run exposed: one cathode pushing current,
three grounded electrodes pulling it back.

```
   Force I  (−20 mA, then −35 mA)
      │
      ▼ ◄──────────────────────  V_cathode measured here (−0.611 / −0.671 V)
  ┌────────┐
  │ SMU1   │
  │Cathode │
  └───┬────┘
      ▽   R_series   ← lead + contact + spreading  =  "Rs"  (ohmic, linear in I)
      │
    ──┴──  diode junction      V_j = n·Vt·ln(I/Is)  (logarithmic in I)
      │
  ┌───┼─────┬─────────┐
  │   │     │         │     return splits (KCL), every terminal held at 0 V:
Anode Guard Group
 2.4   6.0  11.5 mA   @ −20 mA   →  Σ = 19.9 mA = |I_cathode|
 3.8  12.4  18.5 mA   @ −35 mA   →  Σ = 34.7 mA
```

### 6.4 Diagram — why the answer barely moves

```
 measured ΔV = 59.7 mV  over  ΔI = 15 mA   ⇒  secant 3.98 Ω
 ┌───────────────────────────┬───────────────────────────┐
 │  diode log term            │  ohmic term                │
 │  n·Vt·ln(I2/I1) = 29.1 mV  │  I·Rs        = 30.6 mV      │
 │  → 1.94 Ω                   │  → 2.04 Ω                   │
 ├───────────────────────────┼───────────────────────────┤
 │  depends ONLY on           │  the remainder we *call*   │
 │  n, Vt, I1, I2             │  "Rs"                       │
 │  → identical every shot    │  → ≈ constant (see below)  │
 └───────────────────────────┴───────────────────────────┘
```

### 6.5 Three locks that pin the result, in order of importance

1. **The subtracted term is a hard constant.** `n·Vt·ln(I2/I1)/(I2−I1)` contains
   *no device quantity at all* — `n=2`, `Vt=26 mV` are hardcoded, `I1, I2` fixed.
   So **1.94 Ω is the same number on every pin/device/run.** Half the answer is a
   constant before the DUT is touched.

2. **Identical diodes give an identical secant.** The only device-dependent input
   is `ΔV/ΔI`. Nominally identical junctions at identical forced currents sit on
   the same I–V curve → same ~3.98 Ω slope. Measured per-pin spread in `ΔV` is
   **<0.1 mV** (Row 1 ΔV=59.65, Row 2 ΔV=59.71 mV), which is why Rs moves only
   2.036 → 2.040 Ω — the 3rd-digit wiggle is the *entire* device signal.

3. **The return network is shared.** From §5b, the current returns through
   anode ‖ guard ‖ group plus matrix relays and probe leads — mostly **common
   hardware** across pins. So even the ohmic 2.04 Ω is dominated by a fixture/
   return network that is nearly identical for every pin.

Net: `Rs = (≈constant secant) − (exactly-constant model term) ≈ 2 Ω`.

### 6.6 Series Resistance vs Dark Current — same point, different interpretation

Both tests probe the **same operating point of the same network** (reciprocity
proven in §5: force −0.611 V → −19.9 mA ≈ force −20 mA → −0.611 V). The
difference is only what is reported:

- **Dark Current (force V)** reports the *currents*, which are genuinely
  device/pin-sensitive — the split changes per pin (Anode 2.4 mA on OUT1 vs
  1.36 mA on IN2). Real per-pin information survives.
- **Series Resistance (force I)** subtracts a fixed 1.94 Ω model term. What
  survives is `ΔV − 29.1 mV`, and `ΔV` is the same ~59.7 mV on every identical
  diode, so the per-pin information Dark Current *shows* is collapsed into one
  scalar that the model term then largely cancels.

The ~2 Ω is a real lumped series/contact/spreading/return resistance of the
common fixture+junction path; it just can't resolve individual devices because
half of it is a hardcoded constant and the rest is shared hardware.

### 6.7 What *would* move it (the test isn't dead)

- A bad contact/probe on one pin → extra ohmic drop → `ΔV` jumps → Rs jumps.
  **Gross faults are still caught.**
- A device with a different junction (wrong area, defect) → different `ΔV` →
  different Rs.
- Changing assumed `n` → rigidly shifts every Rs (n=1 gives ~3 Ω from the *same*
  data). This is the model-lock; it's why the absolute value isn't a trustworthy
  true device Rs.

What it *cannot* do is resolve small, real per-device series-resistance
differences — those live in the part of `ΔV` swamped by the fixed 1.94 Ω
subtraction and the shared return path.
