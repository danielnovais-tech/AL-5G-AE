#!/usr/bin/env python3
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false
"""
Fine-tune a causal LM (Phi-2, TinyLlama, etc.) on a 5G domain dataset using LoRA.

Dataset format:  JSONL with "instruction" and "output" fields per line.
  {"instruction": "What is PFCP?", "output": "PFCP (Packet Forwarding Control Protocol) ..."}

Usage:
  python finetune.py --dataset data/5g_qa.jsonl --model microsoft/phi-2 --output-dir ./lora_adapter
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("finetune")


def load_dataset_jsonl(path: str) -> List[Dict[str, str]]:
    """Load JSONL dataset and format as chat prompts."""
    records: List[Dict[str, str]] = []
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                logger.warning("Skipping line %d: %s", lineno, exc)
                continue
            instruction = obj.get("instruction", "")
            output = obj.get("output", "")
            if not instruction or not output:
                logger.warning("Skipping line %d: missing instruction or output", lineno)
                continue
            text = f"<|user|>\n{instruction}\n<|assistant|>\n{output}"
            records.append({"text": text})
    logger.info("Loaded %d training examples from %s", len(records), path)
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description="Fine-tune a causal LM with LoRA for 5G domain")
    parser.add_argument("--dataset", required=True, help="JSONL file (instruction / output)")
    parser.add_argument("--model", default="microsoft/phi-2", help="Base model")
    parser.add_argument("--output-dir", default="./lora_adapter", help="Where to save the adapter")
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--lr", type=float, default=2e-4)
    parser.add_argument("--max-length", type=int, default=512)
    parser.add_argument("--lora-r", type=int, default=8)
    parser.add_argument("--lora-alpha", type=int, default=32)
    parser.add_argument("--lora-dropout", type=float, default=0.05)
    parser.add_argument("--fp16", action="store_true", default=True)
    parser.add_argument("--no-fp16", dest="fp16", action="store_false")
    args = parser.parse_args()

    # ---- Validate dataset ----
    if not Path(args.dataset).exists():
        logger.error("Dataset file not found: %s", args.dataset)
        sys.exit(1)

    records = load_dataset_jsonl(args.dataset)
    if len(records) < 2:
        logger.error("Need at least 2 training examples; got %d", len(records))
        sys.exit(1)

    # ---- Late imports (heavy) ----
    try:
        import torch
        from transformers import (  # type: ignore[import-untyped]
            AutoModelForCausalLM,
            AutoTokenizer,
            DataCollatorForLanguageModeling,
            Trainer,
            TrainingArguments,
        )
        from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training  # type: ignore[import-untyped]
        from datasets import Dataset  # type: ignore[import-untyped]
    except ImportError as exc:
        logger.error(
            "Missing dependency: %s\n"
            "Install with:  pip install transformers peft datasets bitsandbytes torch",
            exc,
        )
        sys.exit(1)

    # ---- Load tokenizer & model ----
    logger.info("Loading base model %s …", args.model)
    tokenizer: Any = AutoTokenizer.from_pretrained(args.model, trust_remote_code=True)  # type: ignore[no-untyped-call]
    if tokenizer.pad_token is None:  # type: ignore[union-attr]
        tokenizer.pad_token = tokenizer.eos_token  # type: ignore[union-attr]

    model: Any = AutoModelForCausalLM.from_pretrained(  # type: ignore[no-untyped-call]
        args.model,
        torch_dtype=torch.float16 if args.fp16 else torch.float32,
        device_map="auto",
        trust_remote_code=True,
    )
    model = prepare_model_for_kbit_training(model)  # type: ignore[no-untyped-call]

    # ---- LoRA ----
    lora_config: Any = LoraConfig(
        r=args.lora_r,
        lora_alpha=args.lora_alpha,
        target_modules=["q_proj", "v_proj", "k_proj", "o_proj"],
        lora_dropout=args.lora_dropout,
        bias="none",
        task_type="CAUSAL_LM",
    )
    model = get_peft_model(model, lora_config)  # type: ignore[no-untyped-call]
    model.print_trainable_parameters()  # type: ignore[union-attr]

    # ---- Dataset ----
    dataset: Any = Dataset.from_list(records)  # type: ignore[no-untyped-call]
    tokenized: Any = dataset.map(
        lambda x: tokenizer(  # type: ignore[misc]
            x["text"], truncation=True, max_length=args.max_length, padding="max_length"
        ),
        batched=True,
        remove_columns=["text"],
    )

    # ---- Training ----
    training_args = TrainingArguments(
        output_dir=args.output_dir,
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        learning_rate=args.lr,
        logging_steps=10,
        save_strategy="epoch",
        fp16=args.fp16,
        report_to="none",
    )

    trainer: Any = Trainer(
        model=model,
        args=training_args,
        train_dataset=tokenized,  # type: ignore[arg-type]
        data_collator=DataCollatorForLanguageModeling(tokenizer, mlm=False),  # type: ignore[arg-type]
    )

    logger.info("Starting training (%d epochs, batch size %d) …", args.epochs, args.batch_size)
    trainer.train()  # type: ignore[no-untyped-call]

    # ---- Save ----
    model.save_pretrained(args.output_dir)  # type: ignore[union-attr]
    tokenizer.save_pretrained(args.output_dir)  # type: ignore[union-attr]
    logger.info("LoRA adapter saved to %s", args.output_dir)
    print(f"\nDone. Load the adapter with:\n  model = PeftModel.from_pretrained(base_model, '{args.output_dir}')")


# ---------------------------------------------------------------------------
# Embedding model fine-tuning (sentence-transformers on 5G domain pairs)
# ---------------------------------------------------------------------------

def load_embedding_pairs(path: str) -> List[Dict[str, str]]:
    """Load JSONL with 'query' and 'positive' fields for contrastive training.

    Optionally also 'negative' for harder negatives.
    Format:
      {"query": "What is PFCP?", "positive": "PFCP (Packet Forwarding ...) ...", "negative": "..."}
    """
    pairs: List[Dict[str, str]] = []
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                logger.warning("Skipping line %d: %s", lineno, exc)
                continue
            if "query" not in obj or "positive" not in obj:
                logger.warning("Skipping line %d: missing 'query' or 'positive'", lineno)
                continue
            pairs.append(obj)
    logger.info("Loaded %d embedding training pairs from %s", len(pairs), path)
    return pairs


def finetune_embedding() -> None:
    """Fine-tune a sentence-transformer embedding model on 5G domain pairs."""
    parser = argparse.ArgumentParser(
        description="Fine-tune a sentence-transformer for 5G RAG retrieval"
    )
    parser.add_argument("--dataset", required=True,
                        help="JSONL file with 'query' and 'positive' (optionally 'negative') fields")
    parser.add_argument("--model", default="all-MiniLM-L6-v2",
                        help="Base sentence-transformer model")
    parser.add_argument("--output-dir", default="./embedding_finetuned",
                        help="Where to save the fine-tuned model")
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--lr", type=float, default=2e-5)
    parser.add_argument("--warmup-ratio", type=float, default=0.1)
    args = parser.parse_args()

    if not Path(args.dataset).exists():
        logger.error("Dataset file not found: %s", args.dataset)
        sys.exit(1)

    pairs = load_embedding_pairs(args.dataset)
    if len(pairs) < 2:
        logger.error("Need at least 2 training pairs; got %d", len(pairs))
        sys.exit(1)

    # ---- Late imports ----
    try:
        from sentence_transformers import SentenceTransformer, InputExample, losses  # type: ignore[import-untyped]
        from torch.utils.data import DataLoader  # type: ignore[import-untyped]
    except ImportError as exc:
        logger.error(
            "Missing dependency: %s\n"
            "Install with:  pip install sentence-transformers torch",
            exc,
        )
        sys.exit(1)

    logger.info("Loading base embedding model %s …", args.model)
    st_model: Any = SentenceTransformer(args.model)

    # Build training examples
    has_negatives = all("negative" in p for p in pairs)
    examples: List[Any] = []
    for p in pairs:
        if has_negatives:
            examples.append(InputExample(texts=[p["query"], p["positive"], p["negative"]]))
        else:
            examples.append(InputExample(texts=[p["query"], p["positive"]]))

    train_dataloader: Any = DataLoader(examples, shuffle=True, batch_size=args.batch_size)  # pyright: ignore[reportArgumentType]

    # Choose loss
    if has_negatives:
        train_loss: Any = losses.TripletLoss(model=st_model)
        logger.info("Using TripletLoss (query, positive, negative)")
    else:
        train_loss = losses.MultipleNegativesRankingLoss(model=st_model)
        logger.info("Using MultipleNegativesRankingLoss (query, positive)")

    warmup_steps = int(len(train_dataloader) * args.epochs * args.warmup_ratio)

    logger.info(
        "Starting embedding fine-tuning (%d epochs, batch size %d, lr %.1e) …",
        args.epochs, args.batch_size, args.lr,
    )
    st_model.fit(
        train_objectives=[(train_dataloader, train_loss)],
        epochs=args.epochs,
        warmup_steps=warmup_steps,
        optimizer_params={"lr": args.lr},
        output_path=args.output_dir,
        show_progress_bar=True,
    )

    logger.info("Fine-tuned embedding model saved to %s", args.output_dir)
    print(
        f"\nDone. Use the fine-tuned model with:\n"
        f"  RAG(embedding_model='{args.output_dir}')"
    )


if __name__ == "__main__":
    # Dispatch: if --embedding flag is present, run embedding fine-tuning
    if "--embedding" in sys.argv:
        sys.argv.remove("--embedding")
        finetune_embedding()
    else:
        main()
