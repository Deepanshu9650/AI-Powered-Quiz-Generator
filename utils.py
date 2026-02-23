import os
import json
import PyPDF2
import google.generativeai as genai
from dotenv import load_dotenv
from youtube_transcript_api import YouTubeTranscriptApi

load_dotenv()

# --- GEMINI CONFIGURATION ---
api_key = os.getenv("GEMINI_API_KEY")
if api_key:
    genai.configure(api_key=api_key)
    # Kept gemini-2.5-flash as requested
    model = genai.GenerativeModel('gemini-2.5-flash') 

def extract_text_from_pdf(pdf_file):
    try:
        reader = PyPDF2.PdfReader(pdf_file)
        text = ""
        for i, page in enumerate(reader.pages):
            if i >= 10: break 
            text += page.extract_text() + "\n"
        return text
    except Exception as e:
        return ""

def extract_text_from_youtube(video_url):
    try:
        video_id = ""
        if "v=" in video_url:
            video_id = video_url.split("v=")[1].split("&")[0]
        elif "youtu.be/" in video_url:
            video_id = video_url.split("youtu.be/")[1].split("?")[0]
        elif "shorts/" in video_url:
             video_id = video_url.split("shorts/")[1].split("?")[0]
        
        if not video_id: return None

        try:
            transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
            try:
                transcript = transcript_list.find_transcript(['en', 'en-US'])
            except:
                transcript = next(iter(transcript_list))
                
            full_transcript = transcript.fetch()
            full_text = " ".join([t['text'] for t in full_transcript])
            return full_text
            
        except Exception as e:
            print(f"Transcript Fetch Error: {e}")
            return None
            
    except Exception as e:
        print(f"URL Parsing Error: {e}")
        return None

def generate_study_guide(topic=None, source_text=None):
    if source_text:
        context = f"Source Text: {source_text[:50000]}"
        prompt = f"Create a comprehensive, easy-to-understand study guide and theory overview based on the following text.\n\n{context}\n\nFormat the output using Markdown with clear headings, bullet points, and bold text for key terms. Do not ask questions, just provide the educational material."
    else:
        context = f"Topic: {topic}"
        prompt = f"Create a comprehensive, easy-to-understand study guide and theory overview for the following topic: {context}.\n\nFormat the output using Markdown with clear headings, bullet points, and bold text for key terms. Do not ask questions, just provide the educational material."
    
    try:
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        print(f"-------- GEMINI ERROR --------\n{e}\n------------------------------")
        return "### Error\nCould not generate study guide. Please try again later."

def generate_quiz_questions(topic=None, source_text=None, qcount=5, difficulty="Medium", q_type="MCQ"):
    if q_type == "MCQ":
        json_structure = """[{"question": "...", "options": ["A", "B", "C", "D"], "correct_answer": "Option A", "explanation": "..."}]"""
        type_prompt = "multiple-choice questions"
    elif q_type == "Theory":
        json_structure = """[{"question": "Explain...", "options": [], "correct_answer": "Key points...", "explanation": "..."}]"""
        type_prompt = "short-answer theory questions"
    elif q_type == "Code":
        json_structure = """[{"question": "Write python code...", "options": [], "correct_answer": "def solution():...", "explanation": "..."}]"""
        type_prompt = "coding challenges"
    elif q_type == "Flashcard":
        json_structure = """[{"question": "Concept/Term", "options": [], "correct_answer": "Definition/Answer", "explanation": "..."}]"""
        type_prompt = "flashcards (Concept on front, Definition on back)"
    elif q_type == "Interview":
        json_structure = """[{"question": "Interviewer: ...", "options": [], "correct_answer": "Ideal Candidate Answer: ...", "explanation": "Key concepts to mention..."}]"""
        type_prompt = "behavioral or technical interview questions. Write questions as if an interviewer is asking them."

    context = f"Topic: {topic}" if topic else f"Source Text: {source_text[:50000]}"
    
    prompt = (
        f"Generate {qcount} {type_prompt} based on:\n{context}\n"
        f"Difficulty: {difficulty}.\n"
        f"Return ONLY valid JSON matching this structure:\n{json_structure}"
    )

    try:
        response = model.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
        return response.text.strip()
    except Exception as e:
        print(f"-------- GEMINI ERROR --------\n{e}\n------------------------------")
        return None

def grade_answers_with_ai(qa_pairs):
    prompt = "Grade these answers. Return JSON list: [{'is_correct': true/false, 'feedback': '...'}, ...]\n"
    for i, item in enumerate(qa_pairs):
        prompt += f"Q: {item['question']}\nCorrect: {item['correct_key']}\nStudent: {item['user_answer']}\n---\n"
    try:
        response = model.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
        return json.loads(response.text)
    except:
        return [{"is_correct": False, "feedback": "Error"}] * len(qa_pairs)