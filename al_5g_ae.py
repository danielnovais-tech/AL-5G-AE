#!/usr/bin/env python3
"""
AL-5G-AE: A specialized SLM for 5G Core troubleshooting, log analysis, and
protocol workflows. This script provides an interactive CLI that uses a small
language model (TinyLlama) with a custom system prompt to behave as an expert
copilot for 5G operations.
"""

import argparse
import sys
import warnings

# Suppress some warnings for cleaner output
warnings.filterwarnings("ignore", category=UserWarning, module="transformers")

try:
    from transformers import AutoModelForCausalLM, AutoTokenizer
    import torch
except ImportError:
    print("ERROR: Missing required libraries. Please install them with:")
    print("pip install transformers torch")
    sys.exit(1)

# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------
DEFAULT_MODEL = "TinyLlama/TinyLlama-1.1B-Chat-v1.0"
DEFAULT_DEVICE = "cpu"          # Change to "cuda" if you have a GPU

SYSTEM_PROMPT = """You are AL-5G-AE, a highly specialized small language model for 5G Core operations.
Your expertise includes:
- Troubleshooting 5G Core network functions (AMF, SMF, UPF, NRF, PCF, etc.)
- Interpreting logs, alarms, and signaling traces
- Explaining 5G protocols (NGAP, GTP-U, PFCP, HTTP/2, etc.) and call flows
- Assisting with packet captures and protocol analysis
- Supporting field engineers, NOC, and SOC teams
- Acting as a copilot for 5G Core network maintenance

Answer concisely and accurately, focusing on technical details when asked.
If you don't know the answer, say so instead of guessing.
"""

# ----------------------------------------------------------------------
# Model loading
# ----------------------------------------------------------------------
def load_model(model_name, device):
    """Load tokenizer and model from Hugging Face."""
    print(f"Loading model {model_name}...", file=sys.stderr)
    tokenizer = AutoTokenizer.from_pretrained(model_name, trust_remote_code=True)
    model = AutoModelForCausalLM.from_pretrained(
        model_name,
        torch_dtype=torch.float16 if device == "cuda" else torch.float32,
        device_map="auto" if device == "cuda" else None,
        low_cpu_mem_usage=True,
    )
    if device == "cpu":
        model = model.to("cpu")
    # TinyLlama tokenizer may not have a pad token; set it to eos_token if needed
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    return tokenizer, model


# ----------------------------------------------------------------------
# Generation
# ----------------------------------------------------------------------
def generate_response(tokenizer, model, user_input, max_new_tokens=512, temperature=0.7):
    """Build the chat prompt and generate a response."""
    # Format prompt as a conversation (system + user)
    prompt = f"<|system|>\n{SYSTEM_PROMPT}\n<|user|>\n{user_input}\n<|assistant|>\n"
    inputs = tokenizer(prompt, return_tensors="pt", truncation=True, max_length=2048)
    # Move inputs to the same device as the model
    inputs = {k: v.to(model.device) for k, v in inputs.items()}

    with torch.no_grad():
        outputs = model.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            temperature=temperature,
            do_sample=True,
            top_p=0.9,
            repetition_penalty=1.1,
            pad_token_id=tokenizer.eos_token_id,
        )
    # Decode only the new tokens (skip the input part)
    input_len = inputs["input_ids"].shape[1]
    response = tokenizer.decode(outputs[0][input_len:], skip_special_tokens=True)
    return response.strip()


# ----------------------------------------------------------------------
# Main interactive loop
# ----------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="AL-5G-AE: A specialized SLM for 5G Core troubleshooting."
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Model name or path (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--device",
        default=DEFAULT_DEVICE,
        choices=["cpu", "cuda"],
        help=f"Device to run on (default: {DEFAULT_DEVICE})",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=512,
        help="Maximum number of new tokens to generate (default: 512)",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.7,
        help="Sampling temperature (default: 0.7)",
    )
    args = parser.parse_args()

    # Load model
    try:
        tokenizer, model = load_model(args.model, args.device)
    except Exception as e:
        print(f"Error loading model: {e}", file=sys.stderr)
        sys.exit(1)

    print("\n" + "=" * 60)
    print("AL-5G-AE – 5G Core Specialist Copilot")
    print("=" * 60)
    print("Type your questions or requests. Enter 'quit', 'exit', or Ctrl-D to stop.")
    print("-" * 60)

    while True:
        try:
            user_input = input("\n>>> ")
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            break
        user_input = user_input.strip()
        if user_input.lower() in ("quit", "exit"):
            break
        if not user_input:
            continue

        try:
            response = generate_response(
                tokenizer,
                model,
                user_input,
                max_new_tokens=args.max_tokens,
                temperature=args.temperature,
            )
            print("\n" + response)
        except Exception as e:
            print(f"\n[Error during generation: {e}]")


if __name__ == "__main__":
    main()
