# Quantum mechanics through operational predictions

## One photon in an interferometer

Use two orthonormal states: \(|a\rangle\) = photon in arm A, \(|b\rangle\) = photon in arm B. A lossless beam splitter can be defined by

\[
|a\rangle \to \frac{|a\rangle + i|b\rangle}{\sqrt2},\qquad
|b\rangle \to \frac{i|a\rangle + |b\rangle}{\sqrt2}.
\]

The \(i\) is a phase convention; changing it to \(-i\) changes intermediate signs but never observable probabilities.

Start with one photon in arm A:

\[
|\psi_0\rangle = |a\rangle.
\]

After the first beam splitter:

\[
|\psi_1\rangle = \frac{|a\rangle + i|b\rangle}{\sqrt2}.
\]

A phase shifter in arm B multiplies \(|b\rangle\) by \(e^{i\phi}\):

\[
|\psi_2\rangle = \frac{|a\rangle + i e^{i\phi}|b\rangle}{\sqrt2}.
\]

After the second beam splitter:

\[
|\psi_3\rangle
= \frac12\left[(1-e^{i\phi})|a\rangle + i(1+e^{i\phi})|b\rangle\right].
\]

Born rule: probability of an outcome is the squared magnitude of its amplitude. Therefore

\[
P_A = \left|\frac{1-e^{i\phi}}2\right|^2 = \sin^2(\phi/2),
\]

\[
P_B = \left|\frac{i(1+e^{i\phi})}2\right|^2 = \cos^2(\phi/2).
\]

With \(\phi=0\), all photons exit B. With \(\phi=\pi\), all exit A. Varying \(\phi\) gives sinusoidal interference.

Now add reliable which-path information. After the first beam splitter, entangle the photon with a path detector:

\[
|\psi_1'\rangle = \frac{|a\rangle|D_a\rangle + i|b\rangle|D_b\rangle}{\sqrt2},
\]

where \(|D_a\rangle\) and \(|D_b\rangle\) are orthogonal. After the phase shift and second beam splitter,

\[
|\psi_3'\rangle =
\frac12\left[
|a\rangle|D_a\rangle + i|b\rangle|D_a\rangle
- e^{i\phi}|a\rangle|D_b\rangle + i e^{i\phi}|b\rangle|D_b\rangle
\right].
\]

Because \(|D_a\rangle\) and \(|D_b\rangle\) are orthogonal, the cross terms vanish. The detector probabilities are

\[
P_A = \frac14 + \frac14 = \frac12,\qquad P_B = \frac12,
\]

independent of \(\phi\). The interference is gone.

Why can’t ordinary ignorance about a classical path explain all three cases? If the photon definitely took arm A, the second beam splitter would give \(P_A=P_B=1/2\). If it definitely took arm B, the same. A classical mixture of those two possibilities also gives \(1/2,1/2\), flat in \(\phi\). But with both beam splitters and no which-path record, the observed probabilities are \(\sin^2(\phi/2)\) and \(\cos^2(\phi/2)\). The difference is the interference cross term, which exists only when the two path amplitudes are coherently superposed.

## Amplitudes, superposition, entanglement

A quantum state is a vector in a complex vector space. Each possible measurement outcome has an amplitude, a complex number. The probability is the squared modulus of that amplitude.

Superposition is vector addition. If a state is

\[
|\psi\rangle = \alpha|0\rangle + \beta|1\rangle,
\]

then the probability of finding outcome “0” is \(|\alpha|^2\), and “1” is \(|\beta|^2\). Interference appears when two amplitudes lead to the same final outcome:

\[
|\alpha + \beta|^2 = |\alpha|^2 + |\beta|^2 + 2\operatorname{Re}(\alpha^*\beta).
\]

The last term is interference. It is not “the photon deciding to be a wave or particle”; it is the mathematical consequence of adding amplitudes before squaring.

Entanglement is a non-factorable joint state. For example,

\[
|\Psi\rangle = \frac{|0\rangle|1\rangle - |1\rangle|0\rangle}{\sqrt2}
\]

cannot be written as \((a|0\rangle+b|1\rangle)(c|0\rangle+d|1\rangle)\). Measuring one particle leaves the other in a correlated state. That correlation is real, but it does not by itself transmit information.

## Decoherence and measurement

A measurement is a physical interaction that entangles the system with an apparatus or environment. Suppose a system starts as

\[
\alpha|0\rangle + \beta|1\rangle
\]

and the apparatus starts in \(|A_0\rangle\). After interaction,

\[
\alpha|0\rangle|A_0\rangle + \beta|1\rangle|A_1\rangle.
\]

If \(|A_0\rangle\) and \(|A_1\rangle\) are distinguishable, then the system’s reduced density matrix becomes

\[
\rho_{\rm sys} = |\alpha|^2|0\rangle\langle0| + |\beta|^2|1\rangle\langle1|.
\]

The off-diagonal terms \(\alpha\beta^*|0\rangle\langle1| + \beta\alpha^*|1\rangle\langle0|\) have vanished. This is decoherence.

Operationally, decoherence explains:

- why interference disappears when which-path information is recorded;
- why macroscopic superpositions are not seen in ordinary experiments;
- why measurement records become stable and classical-looking.

Decoherence does **not** explain why one particular outcome occurs. The probabilities are still given by the Born rule. It also does not settle whether “collapse” is a real physical process, whether many worlds are real, or whether hidden variables exist. Those are interpretation-dependent claims. The experimentally agreed predictions are the probabilities and correlations; the interpretation is extra.

## Bell pairs and non-signaling

Use the entangled state

\[
|\Psi\rangle = \frac{|0\rangle|1\rangle - |1\rangle|0\rangle}{\sqrt2}.
\]

Alice measures along direction \(a\), Bob along direction \(b\). Each outcome is \(\pm1\). The correlation is

\[
E(a,b) = -a\cdot b.
\]

For suitable choices of \(a,b\), the CHSH quantity is

\[
S = 2\sqrt2,
\]

which violates the Bell inequality bound \(S \le 2\) for local hidden-variable theories. So Bell experiments rule out any theory in which each particle carries predetermined outcomes for all settings and no influence passes between the two measurements.

But the correlations do not enable faster-than-light signaling. Alice’s outcomes are equally likely \(+1\) and \(-1\), regardless of Bob’s setting. Bob’s outcomes are also equally likely, regardless of Alice’s setting. Before Alice and Bob compare records, each sees only a random sequence. The correlation appears only after they exchange their settings and outcomes using ordinary, light-speed-limited communication.

## Addressing common claims

- **“Observation requires a conscious mind.”** No. A measurement is a physical interaction that creates a stable record. Detectors, atoms, and environments can do that. Consciousness is not needed to derive the predictions. Some interpretations discuss consciousness, but it is not part of the standard experimental formalism.

- **“Delayed choice changes the past.”** No. In a delayed-choice interferometer, the final statistics depend on the complete experimental arrangement. If the second beam splitter is inserted after the photon has passed the first, the photon’s state is still a superposition until a measurement occurs. There is no definite past path to change. The experiment reveals which observable is measured, not a retroactive alteration of history.

- **“Entanglement sends information instantly.”** No. Entangled correlations are nonlocal in the sense that they violate Bell inequalities, but they cannot be used to send a message. Alice cannot choose her outcome; she can only choose her measurement setting. Bob’s marginal statistics do not depend on that setting. No information is transmitted until classical records are compared.

## Three falsifiable predictions

1. In a lossless single-photon Mach-Zehnder interferometer with phase \(\phi\), the detector probabilities are exactly  
   \[
   P_A = \sin^2(\phi/2),\qquad P_B = \cos^2(\phi/2).
   \]
   In particular, with \(\phi=0\), no photon exits A.

2. If reliable which-path information is recorded, the interference disappears:  
   \[
   P_A = P_B = 1/2
   \]
   independent of \(\phi\), as long as the path record is not erased.

3. For an entangled Bell pair, the CHSH correlation exceeds 2 for appropriate settings, while each observer’s individual outcomes remain uniformly random and independent of the other’s settings.

## Where the simplified model omits real detail

Real experiments have imperfect beam splitters, detector inefficiencies, dark counts, phase drift, and multi-photon emission from sources. Bell tests must close detection and locality loopholes, and photon entanglement is usually implemented with polarization or time-bin modes rather than the simple two-level model used here. A fully relativistic treatment of photons also requires quantum field theory, not just single-particle quantum mechanics.

---

`finish_reason=stop; wall_s=929.1; completion_tokens=6792`
