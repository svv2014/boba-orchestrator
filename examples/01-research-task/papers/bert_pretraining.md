# BERT: Pre-training of Deep Bidirectional Transformers (Devlin et al., 2018)

## Abstract

We introduce BERT — Bidirectional Encoder Representations from Transformers. BERT is designed to pre-train deep bidirectional representations from unlabelled text by jointly conditioning on both left and right context in all layers. The pre-trained BERT model can be fine-tuned with just one additional output layer.

## Key Contributions

- **Bidirectional pre-training**: Unlike GPT (left-to-right) or ELMo (shallow concatenation of left-to-right and right-to-left), BERT is deeply bidirectional. This matters: understanding "bank" in "I went to the river bank" requires both left and right context.
- **Masked Language Modelling (MLM)**: 15% of input tokens are masked at random; the model predicts the masked tokens. This enables bidirectional training without the model "seeing itself."
- **Next Sentence Prediction (NSP)**: Model predicts whether two sentences are consecutive. Helps tasks like question answering and natural language inference.

## Architecture

- Base model: 12 transformer layers, 768 hidden, 12 attention heads — 110M parameters.
- Large model: 24 transformer layers, 1024 hidden, 16 attention heads — 340M parameters.
- Pre-trained on BooksCorpus (800M words) + English Wikipedia (2,500M words).

## Results

- **GLUE benchmark**: 80.5 — 7.7% improvement over prior state of the art.
- **SQuAD v1.1 F1**: 93.2 — 1.5 points above human performance.
- **SQuAD v2.0 F1**: 83.1 — 5.1% improvement.

## Why It Matters

BERT established that a single pre-trained model, fine-tuned on a small task-specific dataset, can beat purpose-built architectures. It also demonstrated that bidirectionality is crucial — a result that shifted the entire NLP field toward masked language modelling.
