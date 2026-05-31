from transformers import AutoTokenizer, AutoModelForCausalLM

model_id = "ibm-granite/granite-4.1-3b"

print("Downloading tokenizer...")
tokenizer = AutoTokenizer.from_pretrained(model_id)

print("Downloading model...")
model = AutoModelForCausalLM.from_pretrained(
    model_id,
    device_map="auto"
)

print("Granite downloaded successfully!")