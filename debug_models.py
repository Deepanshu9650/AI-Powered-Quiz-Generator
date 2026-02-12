import google.generativeai as genai
import os
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    print("❌ ERROR: API Key not found in .env file!")
else:
    print(f"✅ Found API Key: {api_key[:5]}...")
    genai.configure(api_key=api_key)

    print("\n🔍 Checking available models for this key...")
    try:
        found_any = False
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                print(f" - {m.name}")
                found_any = True
        
        if not found_any:
            print("⚠️ No content generation models found. Please check your Google AI Studio account.")
            
    except Exception as e:
        print(f"❌ Connection Error: {e}")