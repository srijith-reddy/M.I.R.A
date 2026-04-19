"""Deep-research pipeline building blocks: chunking + hybrid rerank.

The research.deep tool composes these; keeping each stage as a pure-ish
function makes unit testing and future swap-ins (e.g. cross-encoder
rerank, semantic chunking) straightforward."""
