from dotenv import load_dotenv
import os
import google.generativeai as genai

# Load your API key from .env
load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")

# Configure Gemini
genai.configure(api_key=api_key)

# List available models
try:
    models = genai.list_models()
    print("✅ Available Gemini Models:")
    for model in models:
        print(f"- {model.name}")
        print(f"  Supported methods: {model.supported_generation_methods}")
        print()
except Exception as e:
    print("❌ Error listing models:")
    print(e)