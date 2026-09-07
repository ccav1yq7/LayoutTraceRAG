# Historical measurements

These JSON files predate the 0.2.0 correctness changes. They have not been edited to look like
new evaluations. In particular, the older benchmark reranked a larger set than the application graph.
The 0.2.0 graph and benchmark now share candidate fusion; old numbers are not measurements of 0.2.0.

- TVQA-Long main sweep: 18 episodes / 336 questions; subtitle-dominated evaluation.
- Visual ablation: a different 3-episode / 105-question subset.
- Faithfulness: the configured engine's self-check, not independent human fact verification.
- Missing model/data revisions were not reconstructed from guesses.

Run new experiments into a new output file and record the source commit and model/data revisions.
