# Digest — 2026-04-11

#digest

**Today:** 8 sources read (7 HF Daily Papers + 1 book), 10 new concept pages, 5 existing pages enriched.

## Top picks

### [[agent-externalization|Agent Externalization]]
*From "HF Daily Papers — 2026-04-11 Radar Drops" — arXiv 2604.08224*

This paper formalizes what you're already doing with the digital twin: the weights→context→harness progression maps exactly onto the shift from prompt engineering to structured skill definitions and state files. The Norman cognitive-artifacts framing is useful — externalization works because it converts recall into recognition and improvisation into composition. Worth reading because it gives you vocabulary for the design choices you've already made.

→ [[agent-externalization]]

### [[cross-model-capability-transfer|Cross-Model Capability Transfer]]
*From "HF Daily Papers — 2026-04-11 Radar Drops" — arXiv 2604.06377*

Extends features-as-directions from an interp insight into a practical tool: the UNLOCK framework transfers capabilities across models via linear alignment of activation subspaces. 12.1% accuracy gain on MATH transferring reasoning from 14B to 7B, no retraining. The interesting claim is that transfer amplifies what pre-training already learned — post-training doesn't create capabilities so much as sharpen latent ones. Directly relevant to [[sparse-autoencoders-dictionary-learning]] and [[feature-steering]].

→ [[cross-model-capability-transfer]]

### [[agent-failure-diagnosis|Agent Failure Diagnosis]]
*From "HF Daily Papers — 2026-04-11 Radar Drops" — Microsoft Research (AgentRx)*

The nine-category failure taxonomy (plan adherence, information invention, invalid invocation, tool output misinterpretation, intent-plan misalignment, etc.) is immediately useful for debugging any agent harness. The key insight: target the *first unrecoverable critical failure step*, not the symptom step where things visibly break. The constraint-synthesis approach — deriving checks from tool schemas — maps to how you'd want to validate the digital twin's pipeline stages.

→ [[agent-failure-diagnosis]]

## Also added
- [[implicit-memory-in-llms]] — no model exceeds 66% on non-declarative memory; explicit storage doesn't solve implicit memory #evaluation #memory
- [[euthyphro-dilemma]] — is behavior good because it's rewarded (RLHF), or rewarded because it's good (constitutional AI)? The ur-alignment question #philosophy #ethics
- [[socratic-ignorance]] — knowing the limits of your knowledge matters more than the knowledge itself; the philosophical ancestor of calibration research #philosophy #epistemology
- [[social-contract-and-obedience-to-law]] — "persuade or obey" as the philosophical foundation for agent trust boundaries #philosophy #ethics
- [[learning-as-recollection]] — Plato's anamnesis as the classical version of the pretraining/fine-tuning distinction; few-shot prompting as Socratic questioning #philosophy #epistemology
- [[knowledge-vs-true-opinion]] — LLM outputs as Daedalus statues: correct answers that "run away" without chain-of-thought to tie them down #philosophy #epistemology
- [[philosophy-as-practice-for-death]] — Socrates' counter to Nagel's deprivation account; the "risk the belief" passage on acting under uncertainty without claiming certainty #philosophy #metaphysics

## Existing pages enriched
- [[tool-interface-engineering]] — new perspective on meta-cognitive tool use (knowing when *not* to call tools) from Act Wisely / HDPO
- [[cot-faithfulness]] — new perspective on SFT vs RL generalization; asymmetric finding that reasoning improves while safety degrades during SFT
- [[death-as-negative-evil]] — Socrates' opposed view: death as liberation, not deprivation
- [[a-priori-knowledge-and-empiricism-rationalism]] — Plato's anamnesis as the historical origin of the rationalist position
- [[knowledge-error-and-probable-opinion]] — Plato's original formulation of knowledge as "true opinion tied down by an account"

## Filtered out
Radar saw 48 items below the relevance threshold. See
[[../../extracts/radar/2026-04-11|today's radar audit log]] for titles
and links if you want to override and pull any of them in manually.
