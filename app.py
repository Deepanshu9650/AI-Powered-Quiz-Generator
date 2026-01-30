import os
import json
import PyPDF2 
import markdown
import io
from datetime import datetime, date, timedelta
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin, login_user, LoginManager, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import google.generativeai as genai
from dotenv import load_dotenv

# PDF Gen
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "super_secret_key_change_me")
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///quiz.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# --- CONFIG ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login' 

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

api_key = os.getenv("GEMINI_API_KEY")
if api_key:
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-2.5-flash')

# ------------------ DATABASE MODELS ------------------
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(150), nullable=False)
    
    current_streak = db.Column(db.Integer, default=0)
    longest_streak = db.Column(db.Integer, default=0)
    last_quiz_date = db.Column(db.Date, nullable=True) 
    
    history = db.relationship('QuizResult', backref='student', lazy=True)
    # [NEW] Relationship for badges
    achievements = db.relationship('Achievement', backref='owner', lazy=True)

class Achievement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False) # e.g. "Sniper"
    description = db.Column(db.String(200), nullable=False) # e.g. "Score 100%"
    icon = db.Column(db.String(50), nullable=False) # FontAwesome class e.g. "fa-bullseye"
    date_earned = db.Column(db.DateTime, default=datetime.utcnow)

class Question(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.Text, nullable=False)
    q_type = db.Column(db.String(20), default='MCQ') 
    options = db.Column(db.Text, nullable=True) 
    correct_answer = db.Column(db.Text, nullable=False) 
    explanation = db.Column(db.Text, nullable=True)

class QuizResult(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    score = db.Column(db.Integer, nullable=False)
    total_questions = db.Column(db.Integer, nullable=False)
    topic = db.Column(db.String(200), nullable=False)
    difficulty = db.Column(db.String(50), default="Medium")
    date_taken = db.Column(db.DateTime, default=datetime.utcnow)
    details = db.Column(db.Text, nullable=True)

# ------------------ HELPER FUNCTIONS ------------------

# [NEW] Check & Award Badges
def check_achievements(user, result):
    badges_earned = []
    existing_badges = [a.name for a in user.achievements]

    # 1. First Quiz Badge
    if "First Steps" not in existing_badges:
        new_badge = Achievement(user_id=user.id, name="First Steps", description="Completed your first quiz", icon="fa-shoe-prints")
        db.session.add(new_badge)
        badges_earned.append("First Steps")

    # 2. Perfect Score Badge
    if result.score == result.total_questions and result.total_questions >= 5 and "Sniper" not in existing_badges:
        new_badge = Achievement(user_id=user.id, name="Sniper", description="Scored 100% on a quiz (min 5 Qs)", icon="fa-crosshairs")
        db.session.add(new_badge)
        badges_earned.append("Sniper")

    # 3. Streak Badge (3 Days)
    if user.current_streak >= 3 and "On Fire" not in existing_badges:
        new_badge = Achievement(user_id=user.id, name="On Fire", description="Reached a 3-day streak", icon="fa-fire")
        db.session.add(new_badge)
        badges_earned.append("On Fire")

    # 4. Dedication Badge (10 Quizzes Total)
    total_quizzes = len(user.history) # Note: history includes the current one usually
    if total_quizzes >= 10 and "Dedicated" not in existing_badges:
        new_badge = Achievement(user_id=user.id, name="Dedicated", description="Completed 10 quizzes", icon="fa-dumbbell")
        db.session.add(new_badge)
        badges_earned.append("Dedicated")

    if badges_earned:
        db.session.commit()
        # Flash a special message
        flash(f"🏆 Achievement Unlocked: {', '.join(badges_earned)}!", "success")

def update_user_streak(user):
    today = date.today()
    if user.last_quiz_date is None:
        user.current_streak = 1
        user.longest_streak = 1
        user.last_quiz_date = today
        return

    delta = today - user.last_quiz_date
    if delta.days == 0:
        pass 
    elif delta.days == 1:
        user.current_streak += 1
        if user.current_streak > user.longest_streak:
            user.longest_streak = user.current_streak
    else:
        user.current_streak = 1
    
    user.last_quiz_date = today
    db.session.commit()

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

def generate_quiz_questions(topic=None, source_text=None, qcount=5, difficulty="Medium", q_type="MCQ"):
    
    # Define Schema based on type
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
        # [NEW] Flashcard Schema
        json_structure = """[{"question": "Concept/Term", "options": [], "correct_answer": "Definition/Answer", "explanation": "..."}]"""
        type_prompt = "flashcards (Concept on front, Definition on back)"

    context = f"Topic: {topic}" if topic else f"Source Text: {source_text[:50000]}"
    
    prompt = (
        f"Generate {qcount} {type_prompt} based on:\n{context}\n"
        f"Difficulty: {difficulty}.\n"
        f"Return ONLY valid JSON matching this structure:\n{json_structure}"
    )

    try:
        response = model.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
        return response.text.strip()
    except:
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

# ------------------ ROUTES ------------------

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if User.query.filter_by(username=username).first():
            flash('Username exists.', 'danger')
            return redirect(url_for('register'))
        new_user = User(username=username, password=generate_password_hash(password, method='pbkdf2:sha256'))
        db.session.add(new_user)
        db.session.commit()
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form.get('username')).first()
        if user and check_password_hash(user.password, request.form.get('password')):
            login_user(user)
            return redirect(url_for('index'))
        flash('Invalid credentials.', 'danger')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/')
@login_required 
def index():
    history = QuizResult.query.filter_by(user_id=current_user.id).order_by(QuizResult.date_taken.desc()).all()
    return render_template('index.html', user=current_user, history=history)

@app.route('/leaderboard')
@login_required
def leaderboard():
    top_users = User.query.filter(User.current_streak > 0).order_by(User.current_streak.desc()).limit(10).all()
    return render_template('leaderboard.html', leaders=top_users)

@app.route('/profile')
@app.route('/profile/<username>')
@login_required
def profile(username=None):
    if username:
        user_obj = User.query.filter_by(username=username).first_or_404()
    else:
        user_obj = current_user

    history = QuizResult.query.filter_by(user_id=user_obj.id).order_by(QuizResult.date_taken.desc()).all()
    
    activity_data = {}
    for h in history:
        day_str = h.date_taken.strftime('%Y-%m-%d')
        activity_data[day_str] = activity_data.get(day_str, 0) + 1
        
    return render_template('profile.html', user=user_obj, history=history, activity_json=json.dumps(activity_data))

@app.route('/download_result/<int:result_id>')
@login_required
def download_result(result_id):
    result = QuizResult.query.get_or_404(result_id)
    if result.user_id != current_user.id:
        return redirect(url_for('profile'))

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    elements = []
    styles = getSampleStyleSheet()

    elements.append(Paragraph(f"Quiz Report: {result.topic}", styles['Title']))
    elements.append(Paragraph(f"Score: {result.score}/{result.total_questions}", styles['Normal']))
    elements.append(Spacer(1, 20))

    if result.details:
        details = json.loads(result.details)
        for i, item in enumerate(details):
            elements.append(Paragraph(f"<b>Q{i+1}: {item.get('question')}</b>", styles['Heading3']))
            elements.append(Paragraph(f"Your Answer: {item.get('selected')}", styles['Normal']))
            if not item.get('is_correct'):
                elements.append(Paragraph(f"Correct: {item.get('correct')}", styles['Normal']))
            elements.append(Spacer(1, 10))

    doc.build(elements)
    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name=f"Result_{result_id}.pdf", mimetype='application/pdf')

@app.route('/generate_quiz', methods=['POST'])
@login_required
def generate_quiz():
    q_limit = int(request.form.get('question_limit', 5))
    difficulty = request.form.get('difficulty', 'Medium')
    q_type = request.form.get('q_type', 'MCQ')
    
    session['current_difficulty'] = difficulty
    session['q_type'] = q_type 
    session['duration'] = int(request.form.get('duration', 60))

    quiz_json_text = None
    if 'pdf_file' in request.files and request.files['pdf_file'].filename != '':
        file = request.files['pdf_file']
        pdf_text = extract_text_from_pdf(file)
        if pdf_text:
            session['last_topic'] = f"PDF: {file.filename}"
            quiz_json_text = generate_quiz_questions(source_text=pdf_text, qcount=q_limit, difficulty=difficulty, q_type=q_type)
    elif 'topic' in request.form:
        topic = request.form['topic']
        session['last_topic'] = topic
        quiz_json_text = generate_quiz_questions(topic=topic, qcount=q_limit, difficulty=difficulty, q_type=q_type)

    if not quiz_json_text: return redirect(url_for('index'))

    try:
        questions_data = json.loads(quiz_json_text)
        Question.query.delete()
        for q in questions_data:
            options_val = "||".join(q['options']) if q.get('options') else ""
            db.session.add(Question(text=q['question'], q_type=q_type, options=options_val, correct_answer=q['correct_answer'], explanation=q.get('explanation', '')))
        db.session.commit()
        
        # [NEW] Redirect to Flashcards if type is flashcard
        if q_type == 'Flashcard':
            return redirect(url_for('flashcards'))
            
        return redirect(url_for('quiz'))
    except:
        return redirect(url_for('index'))

# [NEW] Flashcards Route
@app.route('/flashcards')
@login_required
def flashcards():
    questions = Question.query.all()
    if not questions: return redirect(url_for('index'))
    return render_template('flashcards.html', cards=questions)

@app.route('/quiz')
@login_required
def quiz():
    questions = Question.query.all()
    return render_template('quiz.html', questions=questions, q_type=session.get('q_type', 'MCQ'), duration=session.get('duration', 60))

@app.route('/submit', methods=['POST'])
@login_required
def submit():
    questions = Question.query.all()
    q_type = session.get('q_type', 'MCQ')
    results = []
    score = 0
    grading_queue = []
    
    for q in questions:
        user_response = request.form.get(str(q.id)) or ""
        if q_type == 'MCQ':
            is_correct = (user_response == q.correct_answer)
            if is_correct: score += 1
            results.append({
                'question': q.text, 'selected': user_response, 'correct': q.correct_answer,
                'is_correct': is_correct, 'explanation': q.explanation, 'options': q.options.split("||") if q.options else []
            })
        else:
            grading_queue.append({"question": q.text, "user_answer": user_response, "correct_key": q.correct_answer})

    if q_type != 'MCQ' and grading_queue:
        ai_grades = grade_answers_with_ai(grading_queue)
        for i, grade in enumerate(ai_grades):
            q = questions[i]
            is_correct = grade.get('is_correct', False)
            if is_correct: score += 1
            results.append({
                'question': q.text, 'selected': grading_queue[i]['user_answer'], 'correct': q.correct_answer,
                'is_correct': is_correct, 'explanation': grade.get('feedback', q.explanation), 'options': []
            })

    new_result = QuizResult(
        user_id=current_user.id,
        score=score,
        total_questions=len(questions),
        topic=session.get('last_topic', 'General'),
        difficulty=session.get('current_difficulty', 'Medium'),
        details=json.dumps(results)
    )
    db.session.add(new_result)
    
    # [NEW] Check Badges logic
    update_user_streak(current_user)
    check_achievements(current_user, new_result)
    
    db.session.commit()
    
    return render_template('result.html', score=score, total=len(questions), results=results, q_type=q_type)

@app.route('/quit')
def quit_quiz(): return redirect(url_for('index'))

@app.template_filter('markdown')
def markdown_filter(text): return markdown.markdown(text or "")

with app.app_context(): db.create_all()

if __name__ == '__main__': app.run(debug=True)