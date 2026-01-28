from dotenv import load_dotenv
import os
import google.generativeai as genai

load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    raise RuntimeError("GEMINI_API_KEY not found in .env")

genai.configure(api_key=api_key)

# Use the correct model name
model = genai.GenerativeModel('gemini-1.5-pro')

try:
    response = model.generate_content("Say hello from Gemini AI")
    print("✅ Gemini responded successfully!")
    print("Response:\n", response.text)
except Exception as e:
    print("❌ Error communicating with Gemini API:")
    print(e)