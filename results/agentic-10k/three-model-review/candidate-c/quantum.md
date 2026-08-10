# Quantum mechanics through operational predictions

### Quantum Mechanics for a Software Engineer

Quantum mechanics is a theory of amplitudes and probabilities. It describes systems using vectors in a Hilbert space, where complex amplitudes determine measurement probabilities via the Born rule. We’ll build this around a concrete experiment: the single-photon Mach-Zehnder interferometer.

---

#### 1. Mach-Zehnder Interferometer

**Setup:** A photon enters a beam splitter (BS1), splits into two paths (upper and lower), reflects off mirrors, recombines at a second beam splitter (BS2), and is detected at two detectors (D0 and D1). We model the photon’s state as a vector in a 2D Hilbert space:  
- \(|0\rangle\): upper path  
- \(|1\rangle\): lower path  

**Beam-splitter convention:** We use a 50:50 beam splitter with the unitary transformation:  
\[
U = \frac{1}{\sqrt{2}} \begin{pmatrix} 1 & i \\ i & 1 \end{pmatrix}
\]  
This means:  
- \(U|0\rangle = \frac{1}{\sqrt{2}}(|0\rangle + i|1\rangle)\)  
- \(U|1\rangle = \frac{1}{\sqrt{2}}(i|0\rangle + |1\rangle)\)  

The phase factor \(i\) (a 90° shift) is a convention; other conventions change intermediate signs but not observable probabilities.

**State evolution:**  
1. After BS1: \(|\psi_1\rangle = \frac{1}{\sqrt{2}}(|0\rangle + i|1\rangle)\).  
2. Add a phase shift \(\phi\) in the upper path: \(|\psi_2\rangle = \frac{1}{\sqrt{2}}(e^{i\phi}|0\rangle + i|1\rangle)\).  
3. After BS2: apply \(U\) to \(|\psi_2\rangle\):  
\[
|\psi_3\rangle = U|\psi_2\rangle = \frac{1}{2}\left[(e^{i\phi} - 1)|0\rangle + i(e^{i\phi} + 1)|1\rangle\right].
\]

**Probabilities (Born rule):**  
- \(P(D0) = |\langle 0|\psi_3\rangle|^2 = \frac{1}{4}|e^{i\phi} - 1|^2 = \sin^2(\phi/2)\)  
- \(P(D1) = |\langle 1|\psi_3\rangle|^2 = \frac{1}{4}|e^{i\phi} + 1|^2 = \cos^2(\phi/2)\)  

**(a) Both beam splitters, no phase shift (\(\phi=0\)):**  
\(P(D0)=0\), \(P(D1)=1\). All photons go to D1. This is interference: the amplitudes from the two paths cancel at D0 and add at D1.

**(b) Phase shift \(\phi\):**  
Probabilities vary sinusoidally with \(\phi\), showing interference.

**(c) Which-path information:**  
If we add a which-path detector (e.g., a device that records whether the photon took the upper path), the photon becomes entangled with the detector. The combined state becomes \(\frac{1}{\sqrt{2}}(|0\rangle|W'\rangle + i|1\rangle|W\rangle)\), where \(|W\rangle\) and \(|W'\rangle\) are detector states. After BS2, the photon state is a mixture of two paths with no coherence. The probabilities become \(P(D0)=P(D1)=1/2\) regardless of \(\phi\). Interference vanishes because the which-path information destroys the superposition.

**Why classical ignorance fails:**  
If the photon were a classical particle taking one path with probability 1/2, the probability at D0 would be the average of the two paths: \(P(D0) = \frac{1}{2}P(D0|\text{upper}) + \frac{1}{2}P(D0|\text{lower})\). With no phase shift, each path would give 50% to D0, so \(P(D0)=50\%\). But we observe \(P(D0)=0\). The quantum prediction uses amplitudes that add, not probabilities. Classical ignorance cannot reproduce the interference pattern.

---

#### 2. Amplitudes, Born Rule, Superposition, Entanglement

- **Amplitudes:** Complex numbers \(\langle a|\psi\rangle\) that encode the probability of outcome \(a\).  
- **Born rule:** Probability \(P(a) = |\langle a|\psi\rangle|^2\).  
- **Superposition:** If \(|\psi_1\rangle\) and \(|\psi_2\rangle\) are possible states, then \(\alpha|\psi_1\rangle + \beta|\psi_2\rangle\) is also a state (with \(|\alpha|^2+|\beta|^2=1\)). Amplitudes add, leading to interference.  
- **Entanglement:** A composite state that cannot be written as a product of individual states, e.g., \(|\Phi^+\rangle = \frac{1}{\sqrt{2}}(|00\rangle + |11\rangle)\). Entanglement produces correlations that violate classical bounds.

We avoid “wave or particle” language: the photon is a quantum system described by a state vector; measurements yield probabilities.

---

#### 3. Decoherence and Measurement

**Decoherence:** Interaction with the environment causes the off-diagonal terms in the density matrix to decay, effectively turning a superposition into a classical mixture. Operationally, it explains why macroscopic superpositions are not observed: the environment “measures” the system, suppressing interference. Decoherence does not solve the measurement problem—it explains the emergence of classicality but not why a single outcome occurs. Different interpretations (Copenhagen, many-worlds, etc.) give different answers.

**Experimentally agreed predictions:**  
- Interference disappears when which-path information is available.  
- Decoherence timescales can be calculated and measured (e.g., for a qubit in a noisy environment).  

**Interpretation-dependent claims:**  
- Whether the wave function collapses or branches.  
- The role of consciousness in measurement (see below).

---

#### 4. Bell-Pair Experiment

**Setup:** Two entangled photons in the state \(|\Phi^+\rangle = \frac{1}{\sqrt{2}}(|HH\rangle + |VV\rangle)\), where H and V denote horizontal and vertical polarization. Alice and Bob each measure polarization along chosen directions.

**Bell inequality:** For any local hidden variable theory, the correlation \(E(a,b)\) between measurements satisfies \(|S| \leq 2\) (CHSH inequality). Quantum mechanics predicts \(S = 2\sqrt{2}\) for optimal angles, violating the inequality. This rules out local hidden variable theories.

**Why no faster-than-light signaling:** Each observer’s outcomes are random and uncorrelated until they compare results. Before comparison, Alice sees a random sequence of H/V outcomes; Bob sees the same. The correlations only appear when they compare records, which requires classical communication. Thus, no information is transmitted faster than light.

**What each observer sees before comparing:** Random outcomes with no pattern. The correlations are only revealed after classical communication.

---

#### 5. Addressing Claims

- **“Observation requires a conscious mind.”**  
  No. Decoherence and measurement are physical processes. Consciousness is not required; the measurement problem is about the interpretation of the wave function, not about minds.

- **“Delayed choice changes the past.”**  
  No. In delayed-choice experiments, the choice of measurement is made after the photon enters the interferometer, but it affects the future measurement outcome, not the past. The photon’s behavior is determined by the measurement context, not by retroactive change.

- **“Entanglement sends information instantly.”**  
  No. Entanglement produces instantaneous correlations, but no information can be transmitted because outcomes are random and cannot be controlled. To extract information, one must compare results, which requires classical communication.

---

#### 6. Falsifiable Predictions and Omissions

**Three falsifiable predictions:**  
1. In a Mach-Zehnder interferometer with phase shift \(\phi\), \(P(D0) = \sin^2(\phi/2)\). This can be tested by varying \(\phi\) and measuring counts.  
2. In a Bell experiment with entangled photons, the CHSH inequality will be violated, with \(S > 2\). This can be tested by measuring correlations at different angles.  
3. Decoherence timescales for a quantum system (e.g., a superconducting qubit) can be predicted and measured, matching theoretical calculations.

**Two omissions:**  
1. We ignored finite photon bandwidth and dispersion effects.  
2. We did not detail the exact dynamics of beam splitters, such as polarization-dependent phase shifts.

---

This explanation builds quantum mechanics from a concrete experiment, using linear algebra and probability, and connects to key concepts without relying on classical analogies.

---

`finish_reason=stop; wall_s=142.0; completion_tokens=5223`
