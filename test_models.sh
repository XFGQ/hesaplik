#!/bin/bash

source .env

# Test edilecek modellerin listesi (Buraya listeni ekleyebilirsin)
MODELS=(
  "deepseek-ai/deepseek-coder-6.7b-instruct"
  "meta/llama-3.3-70b-instruct"
  "nvidia/llama-3.1-nemotron-70b-instruct"
  "nvidia/nemotron-4-340b-instruct"
)

echo "Modeller test ediliyor..."
echo "-------------------------"

for MODEL in "${MODELS[@]}"; do
  # Sadece HTTP durum kodunu alıyoruz
  HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" https://integrate.api.nvidia.com/v1/chat/completions \
    -H "Authorization: Bearer $NVIDIA_API_KEY" \
    -H "Content-Type: application/json" \
    -d "{\"model\":\"$MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"test\"}],\"max_tokens\":5}")
  
  if [ "$HTTP_STATUS" -eq 200 ]; then
    echo -e "[\e[32mBAŞARILI - 200\e[0m] $MODEL"
  else
    echo -e "[\e[31mBAŞARISIZ - $HTTP_STATUS\e[0m] $MODEL"
  fi
done
