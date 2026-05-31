from transformers import AutoTokenizer, AutoModelForCausalLM

model_id = "ibm-granite/granite-3.3-2b-instruct"

print("Loading model...")

tokenizer = AutoTokenizer.from_pretrained(model_id)
model = AutoModelForCausalLM.from_pretrained(model_id)

prompt = "Explain Formula 1 racing in simple words."

inputs = tokenizer(prompt, return_tensors="pt")

outputs = model.generate(
    **inputs,
    max_new_tokens=100
)

print(tokenizer.decode(outputs[0], skip_special_tokens=True))