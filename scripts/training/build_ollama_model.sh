#!/usr/bin/env bash
# Convert the fused HF model to GGUF (llama.cpp) and register it with Ollama as "finagent-ap".
#   LLAMA_CPP=~/src/llama.cpp scripts/training/build_ollama_model.sh
set -euo pipefail
cd "$(dirname "$0")/../.."
: "${LLAMA_CPP:?set LLAMA_CPP to your llama.cpp checkout}"
python "$LLAMA_CPP/convert_hf_to_gguf.py" training/fused --outfile training/finagent-ap.gguf --outtype q8_0
(cd training && ollama create finagent-ap -f Modelfile)
echo "Run: FINAGENT__LLM__PROVIDER=ollama FINAGENT__LLM__PROVIDERS__OLLAMA__MODEL=finagent-ap finagent eval"
