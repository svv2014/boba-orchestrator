# Attention Is All You Need (Vaswani et al., 2017)

## Abstract

The dominant sequence transduction models are based on complex recurrent or convolutional neural networks that include an encoder and a decoder. The best performing models also connect the encoder and decoder through an attention mechanism. We propose a new simple network architecture, the Transformer, based solely on attention mechanisms, dispensing with recurrence and convolutions entirely.

## Key Contributions

- **Self-attention mechanism**: Relates different positions of a single sequence to compute a representation. Unlike recurrence, self-attention allows O(1) sequential operations regardless of sequence length.
- **Multi-head attention**: Allows the model to jointly attend to information from different representation subspaces at different positions. With a single attention head, averaging inhibits this.
- **Positional encodings**: Since the model contains no recurrence or convolution, positional encodings are injected to give the model information about the relative or absolute position of tokens.

## Architecture

The Transformer follows an encoder-decoder structure. The encoder maps an input sequence to a sequence of continuous representations. Given that representation, the decoder generates an output sequence one element at a time.

- **Encoder**: Stack of 6 identical layers. Each layer has two sub-layers: multi-head self-attention + position-wise fully connected feed-forward network.
- **Decoder**: Stack of 6 identical layers. Each layer has three sub-layers: masked multi-head attention + multi-head attention over encoder output + feed-forward.

## Results

- **WMT 2014 English-to-German**: 28.4 BLEU — exceeds all previously published ensembles.
- **WMT 2014 English-to-French**: 41.0 BLEU — new single-model state of the art.
- **Training cost**: 8 P100 GPUs for 3.5 days (English-German base model).

## Why It Matters

This paper introduced the architecture that underlies GPT, BERT, and virtually every modern large language model. The key insight: attention alone, without recurrence, is sufficient — and far more parallelisable.
