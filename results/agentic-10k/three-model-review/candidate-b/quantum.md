# Quantum mechanics through operational predictions

# Quantum Mechanics for Software Engineers

## 1. The Mach-Zehnder Interferometer

A single photon enters a Mach-Zehnder interferometer through input port **0**. Two beam splitters (BS1, BS2) and two mirrors direct it to two detectors, D₀ and D₁.

**Beam splitter convention** (used throughout): A 50-50 beam splitter transforms input modes as:

$$b^\dagger_{\text{out}} = \frac{1}{\sqrt{2}}(b^\dagger_{\text{in}} + i\,b^\dagger_{\text{in}}')$$

where the factor *i* represents a **π/2 phase shift** on reflection. This is a common convention; swapping the sign of *i* everywhere would flip intermediate amplitudes but leave final probabilities unchanged.

### (a) Both beam splitters, no phase shift

**Step 1 — After BS1:** The photon state evolves to a superposition of the two arms:

$$|1\rangle_0 \;\longrightarrow\; \frac{1}{\sqrt{2}}\bigl(|L\rangle + i|R\rangle\bigr)$$

where |L⟩ and |R⟩ denote the photon being in the left or right arm.

**Step 2 — After the phase shifter (φ = 0):** No change; the state remains (|L⟩ + i|R⟩)/√2.

**Step 3 — After BS2:** Each arm splits again. The left arm reflects to D₁ (amplitude i/√2) and transmits to D₀ (amplitude 1/√2). The right arm reflects to D₀ (amplitude i/√2) and transmits to D₁ (amplitude 1/√2).

Combining paths to each detector:

$$|\text{D}_0\rangle: \quad \frac{1}{\sqrt{2}}\!\left[\underbrace{\frac{1}{\sqrt{2}}}_{\text{L→D₀}} + \underbrace{\frac{i}{\sqrt{2}} \cdot i}_{\text{R→D₀}}\right] = \frac{1}{2\sqrt{2}}(1 - 1) = 0$$

$$|\text{D}_1\rangle: \quad \frac{1}{\sqrt{2}}\!\left[\underbrace{\frac{i}{\sqrt{2}}}_{\text{L→D₁}} + \underbrace{\frac{i}{\sqrt{2}} \cdot 1}_{\text{R→D₁}}\right] = \frac{1}{2\sqrt{2}}(i + i) = \frac{i}{\sqrt{2}}$$

**Probabilities:** P(D₀) = |0|² = 0, P(D₁) = |i/√2|² = 1. The photon *always* reaches D₁.

### (b) Phase shift φ in one arm

Insert a phase shifter (path length change, refractive index, etc.) in the right arm. The state after the phase shifter is:

$$\frac{1}{\sqrt{2}}\bigl(|L\rangle + i\,e^{i\phi}|R\rangle\bigr)$$

Repeating the BS2 calculation:

$$|\text{D}_0\rangle: \quad \frac{1}{2\sqrt{2}}\bigl(1 - e^{i\phi}\bigr)$$

$$|\text{D}_1\rangle: \quad \frac{1}{2\sqrt{2}}\bigl(1 + e^{i\phi}\bigr)$$

**Probabilities:**

$$P(D_0) = \frac{1}{4}|1 - e^{i\phi}|^2 = \frac{1}{4}\bigl[2 - 2\cos\phi\bigr] = \sin^2\!\frac{\phi}{2}$$

$$P(D_1) = \frac{1}{4}|1 + e^{i\phi}|^2 = \frac{1}{4}\bigl[2 + 2\cos\phi\bigr] = \cos^2\!\frac{\phi}{2}$$

As φ varies, detection probabilities oscillate between 0 and 1. This is **interference**: the two path amplitudes add or cancel depending on the relative phase.

### (c) Reliable which-path information exists

Place a detector that unambiguously identifies which arm the photon traveled through. The detector states are |D⟩ (detected) and |∅⟩ (no detection). After BS1:

$$\frac{1}{\sqrt{2}}\bigl(|L\rangle|\text{detector knows L}\rangle + i|R\rangle|\text{detector knows R}\rangle\bigr)$$

The detector state is now **correlated** with the photon path. When you measure the detector, you find either "L" or "R" with equal probability, and the photon will be in the corresponding arm.

**Probabilities:** P(D₀) = P(D₁) = 1/2. The interference pattern vanishes. The phase φ has no effect.

### Why classical ignorance cannot explain this

Suppose you *don't* look at the detector and simply don't know which path the photon took. Classical probability gives:

$$P(D_0) = \tfrac{1}{2}P(\text{photon in L})P(D_0|L) + \tfrac{1}{2}P(\text{photon in R})P(D_0|R) = \tfrac{1}{2} \cdot \tfrac{1}{2} + \tfrac{1}{2} \cdot \tfrac{1}{2} = \tfrac{1}{2}$$

This matches case (c) but **contradicts** cases (a) and (b). Classical ignorance predicts 50-50 statistics regardless of phase. Quantum mechanics predicts phase-dependent interference when paths are **coherent** (amplitudes can add) and 50-50 when paths are **decohered** (amplitudes cannot interfere). The difference is not about what we know—it's about whether the physical state permits interference.

---

## 2. Amplitudes, the Born Rule, Superposition, and Entanglement

**Amplitudes** are complex numbers associated with basis states. They are not probabilities; they *interfere*. The **Born rule** states that the probability of outcome |φᵢ⟩ when measuring state |ψ⟩ in basis {|φᵢ⟩} is:

$$P(i) = |\langle\phi_i|\psi\rangle|^2$$

This is not an extra assumption bolted on—it is how the formalism connects to observable frequencies. In the interferometer, the amplitude for D₀ is the sum of path amplitudes; squaring gives the detection probability.

**Superposition** is linear evolution: if |ψ₁⟩ and |ψ₂⟩ are valid states, so is a₁|ψ₁⟩ + a₂|ψ₂⟩. The coefficients aᵢ are complex amplitudes, not weights in a mixture. In the interferometer, the photon is literally in both arms simultaneously—not in one arm "chosen at random."

**Entanglement** arises when two subsystems interact and their joint state cannot be factored into individual states. For two photons on separate paths:

$$|\Psi^+\rangle = \frac{1}{\sqrt{2}}\bigl(|H\rangle_A|V\rangle_B + |V\rangle_A|H\rangle_B\bigr)$$

Here, |H⟩ and |V⟩ denote horizontal and vertical polarization. No individual quantum state describes photon A or photon B alone; their properties are correlated. Measuring A's polarization instantly constrains B's, even across large distances.

---

## 3. Decoherence and Measurement

**Decoherence** occurs when a quantum system interacts with an environment (air molecules, stray photons, thermal radiation). The environment effectively measures the system, entangling environmental states with the system's basis states:

$$|\psi\rangle|S\rangle_{\text{env}} \;\longrightarrow\; a_1|\phi_1\rangle|E_1\rangle + a_2|\phi_2\rangle|E_2\rangle$$

where {|φᵢ⟩} is the "pointer basis" (typically position or energy eigenstates for macroscopic objects). The environment now carries which-way information. When you compute probabilities by tracing over the environment, cross-terms vanish:

$$\text{Tr}_{\text{env}}(|\Psi\rangle\langle\Psi|) = |a_1|^2|\phi_1\rangle\langle\phi_1| + |a_2|^2|\phi_2\rangle\langle\phi_2|$$

The state becomes a **classical mixture**, not a superposition. Interference effects are suppressed.

**What decoherence explains operationally:**
- Why macroscopic objects appear to have definite positions
- Why we don't observe Schrödinger's-cat-style superpositions in everyday life
- Why certain measurement bases (position, energy) are "natural" while others require careful isolation

**What decoherence does not settle:**
- The **measurement problem**: decoherence produces a mixture, not a definite outcome. You still need a rule to connect "mixture of possibilities" to "one actual result."
- The **Born rule**: decoherence explains why interference vanishes but not why |aᵢ|² gives the frequencies of outcomes.
- The **preferred basis problem**: decoherence doesn't uniquely pick the pointer basis without additional structure.

**Separating agreement from interpretation:** All mainstream interpretations (Copenhagen, Many-Worlds, objective collapse, pilot-wave) agree on decoherence's operational effects and on the Born rule predictions. They disagree on what "really exists" and what the mathematics means physically.

---

## 4. Bell's Theorem

Consider two entangled photons shared between observers Alice and Bob, separated by a large distance. Each photon can be measured in one of two bases. Alice measures either **a** or **a′**; Bob measures either **b** or **b′**.

The **CHSH Bell inequality** constrains any theory where:
1. Outcomes are predetermined (realism)
2. Measurements don't instantaneously affect distant systems (locality)

Specifically, the correlation function E(a, b) satisfies:

$$S = E(a,b) - E(a,b') + E(a',b) + E(a',b') \leq 2$$

Quantum mechanics predicts for optimal settings: S = 2√2 ≈ 2.83.

**What Bell violation rules out:** Any theory that is both local *and* realistic. "Realistic" means physical properties exist before measurement. "Local" means no instantaneous action at a distance. You must abandon at least one.

**No faster-than-light signaling:** Each observer's outcomes appear completely random (50-50) regardless of the other's measurement choice. Alice cannot tell from her local data whether Bob measured b or b′. Only by classically comparing full records do the correlations emerge. The formalism respects relativistic causality.

**What each observer sees before comparing records:** Random bits with no exploitable pattern. The joint statistics are only visible post-comparison.

---

## 5. Addressing Specific Claims

**"Observation requires a conscious mind."**
False. The formalism treats measurement as a physical interaction with a macroscopic apparatus. A photodetector, a cloud chamber, a fluorescent screen—all count as measurements. Whether consciousness plays any role is a philosophical question, not a physical one. All interpretations agree on the predictions; none require conscious observers.

**"Delayed choice changes the past."**
Misleading. In Wheeler's delayed-choice experiment, the second beam splitter's presence or absence is decided *after* the photon has entered the interferometer. The photon "behaves as a wave" when both splitters are present and "as a particle" when only the first is present. But this is a description of the experimental setup, not a causal influence. The photon doesn't "know" the future choice; the setup determines what questions you can ask. No information travels backward.

**"Entanglement sends information instantly."**
False. Entanglement correlations are only visible when classical communication compares records. Each local outcome is random and uncontrollable. You cannot choose what Alice's measurement yields, so you cannot encode a message. The no-signaling theorem proves that local measurements respect the light cone.

---

## 6. Falsifiable Predictions and Omissions

### Three Falsifiable Predictions

1. **Interference visibility** in a Mach-Zehnder interferometer decreases monotonically as environment-induced which-path information becomes available (quantified by the decoherence rate).
2. **Bell inequality violation** with S > 2 for appropriately chosen measurement settings on entangled photon pairs, reproducible across independent laboratories.
3. **Phase dependence** of detector probabilities following P(D₀) = sin²(φ/2), P(D₁) = cos²(φ/2), with the phase shift introduced by a controllable element (piezoelectric mirror, electro-optic modulator, etc.).

### Two Omissions from the Simplified Model

1. **Photon source and detector imperfections.** Real experiments use attenuated lasers or parametric down-conversion, producing states with vacuum components, multi-photon events, and mode structure. Single-photon detectors have finite efficiency (typically 60-90%) and dark counts. The simplified "one photon in, one click out" model ignores these realities.

2. **Full electromagnetic field quantization.** This explanation treats photons as particles in a classical interferometer. A complete treatment requires quantum field theory, where number states are excitations of modes, beam splitters are interactions, and phenomena like spontaneous emission and the Unruh effect emerge naturally. The simplified model cannot describe photon creation/annihilation or relativistic effects.

---

The core lesson: quantum mechanics is a formalism for computing probability amplitudes and their evolution. Its predictions are unambiguous; its interpretation is not. The experiments are real; the philosophical story you tell about them is optional.

---

`finish_reason=stop; wall_s=131.5; completion_tokens=4435`
