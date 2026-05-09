# Scaling Laws for Neural Language Models (Kaplan et al., 2020)

## Abstract

We study empirical scaling laws for language model performance on the cross-entropy loss. The loss scales as a power-law with model size, dataset size, and the amount of compute used for training. Some trends span more than seven orders of magnitude.

## Key Contributions

- **Power-law scaling**: Test loss follows L ∝ N^(-0.076) with model parameters N (all else equal). Doubling model size reduces loss predictably.
- **Compute-optimal frontier**: For a fixed compute budget C, there is an optimal allocation between model size N and training tokens D. Empirically: N ∝ C^0.73, D ∝ C^0.27 — meaning larger models are more compute-efficient than longer training runs.
- **Data requirements scale slowly**: To halve loss from data alone requires 40× more data. Models benefit more from scaling parameters.

## Findings

1. **Model size matters most**: Holding compute fixed, larger models trained for fewer steps outperform smaller models trained longer.
2. **Overfitting is late**: Models rarely overfit in the classical sense; more data almost always helps.
3. **Architecture details matter less than scale**: Depth vs. width tradeoffs within a factor of 2 have minimal effect compared to total parameter count.
4. **Smooth, predictable improvement**: Unlike many ML results, scaling gains are smooth and well-predicted by the power-law formulas — making resource planning tractable.

## Compute-Optimal Implications

If you have C FLOPs to spend, the optimal strategy is to train a model of N* ≈ C^0.73 parameters on D* ≈ C^0.27 tokens. This finding directly motivated the Chinchilla model (Hoffmann et al., 2022), which revisited these laws with larger compute budgets.

## Why It Matters

This paper provided the theoretical justification for the "just scale it" approach that produced GPT-3 and its successors. It also gave practitioners a practical tool: predict final loss before spending the compute, and allocate budget optimally between model size and training duration.
