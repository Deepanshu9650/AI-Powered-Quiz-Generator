import os
import json
import io
import markdown
from datetime import datetime, date
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin, login_user, LoginManager, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet

# --- IMPORT HELPER FUNCTIONS FROM UTILS.PY ---
from utils import (
    extract_text_from_pdf, 
    extract_text_from_youtube, 
    generate_study_guide, 
    generate_quiz_questions, 
    grade_answers_with_ai
)

# --- IMPORT NEW ROUTE FILES (BLUEPRINTS) ---
from future_routes import new_features_bp

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "super_secret_key_change_me")

# Register the Blueprint so Flask knows about your extra .py files
app.register_blueprint(new_features_bp)

# --- DATABASE CONFIGURATION ---
database_url = os.environ.get('DATABASE_URL')
if database_url and database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = database_url or 'sqlite:///quiz.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# --- CONFIG ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login' 

@login_manager.user_loader
def load_user(user_id):
    # Using the modern db.session.get to fix the legacy warning
    return db.session.get(User, int(user_id))

# ------------------ DATABASE MODELS ------------------
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(150), nullable=False)
    current_streak = db.Column(db.Integer, default=0)
    longest_streak = db.Column(db.Integer, default=0)
    last_quiz_date = db.Column(db.Date, nullable=True) 
    history = db.relationship('QuizResult', backref='student', lazy=True)
    achievements = db.relationship('Achievement', backref='owner', lazy=True)

class Achievement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False) 
    description = db.Column(db.String(200), nullable=False) 
    icon = db.Column(db.String(50), nullable=False) 
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

# ------------------ DATABASE HELPER FUNCTIONS ------------------
def check_achievements(user, result):
    badges_earned = []
    existing_badges = [a.name for a in user.achievements]

    if "First Steps" not in existing_badges:
        new_badge = Achievement(user_id=user.id, name="First Steps", description="Completed your first quiz", icon="fa-shoe-prints")
        db.session.add(new_badge)
        badges_earned.append("First Steps")

    if result.score == result.total_questions and result.total_questions >= 5 and "Sniper" not in existing_badges:
        new_badge = Achievement(user_id=user.id, name="Sniper", description="Scored 100% on a quiz (min 5 Qs)", icon="fa-crosshairs")
        db.session.add(new_badge)
        badges_earned.append("Sniper")

    if user.current_streak >= 3 and "On Fire" not in existing_badges:
        new_badge = Achievement(user_id=user.id, name="On Fire", description="Reached a 3-day streak", icon="fa-fire")
        db.session.add(new_badge)
        badges_earned.append("On Fire")

    total_quizzes = len(user.history) 
    if total_quizzes >= 10 and "Dedicated" not in existing_badges:
        new_badge = Achievement(user_id=user.id, name="Dedicated", description="Completed 10 quizzes", icon="fa-dumbbell")
        db.session.add(new_badge)
        badges_earned.append("Dedicated")
    
    if "YouTube" in result.topic and "Video Learner" not in existing_badges:
        new_badge = Achievement(user_id=user.id, name="Video Learner", description="Generated a quiz from a YouTube video", icon="fa-video")
        db.session.add(new_badge)
        badges_earned.append("Video Learner")

    if badges_earned:
        db.session.commit()
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

# ------------------ CORE ROUTES ------------------

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
            return redirect(url_for('dashboard')) 
        flash('Invalid credentials.', 'danger')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))

@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return render_template('index.html')

@app.route('/dashboard')
@login_required 
def dashboard():
    history = QuizResult.query.filter_by(user_id=current_user.id).order_by(QuizResult.date_taken.desc()).all()
    return render_template('dashboard.html', user=current_user, history=history)

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

@app.route('/mistakes')
@login_required
def mistakes():
    imperfect_quizzes = QuizResult.query.filter(
        QuizResult.user_id == current_user.id, 
        QuizResult.score < QuizResult.total_questions
    ).all()
    
    mistake_list = []
    
    for quiz in imperfect_quizzes:
        try:
            details = json.loads(quiz.details)
            for q in details:
                if not q.get('is_correct'):
                    mistake_list.append({
                        'question': q.get('question'),
                        'your_answer': q.get('selected'),
                        'correct_answer': q.get('correct'),
                        'explanation': q.get('explanation'),
                        'topic': quiz.topic,
                        'date': quiz.date_taken
                    })
        except:
            continue
            
    return render_template('mistakes.html', mistakes=mistake_list)

@app.route('/review/<int:result_id>')
@login_required
def review_quiz(result_id):
    result = QuizResult.query.get_or_404(result_id)
    if result.user_id != current_user.id:
        flash("You are not authorized to view this result.", "danger")
        return redirect(url_for('dashboard'))
    try:
        results_data = json.loads(result.details) if result.details else []
    except:
        results_data = []
    return render_template('result.html', score=result.score, total=result.total_questions, results=results_data, q_type="Review")

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

@app.route('/download_guide', methods=['POST'])
@login_required
def download_guide():
    topic = request.form.get('topic', 'Study_Guide')
    material = request.form.get('material', '')
    
    # Generate filename with .pdf extension
    safe_topic = "".join([c for c in topic if c.isalpha() or c.isdigit() or c==' ']).rstrip().replace(' ', '_')
    if not safe_topic: safe_topic = "Study_Guide"
    filename = f"{safe_topic}_Notes.pdf"

    # Set up the PDF Buffer and Document using ReportLab
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()
    
    elements = []
    
    # Title Section
    elements.append(Paragraph(f"Study Guide: {topic}", styles['Title']))
    elements.append(Spacer(1, 12))

    # Convert Markdown to HTML, then strip/process for ReportLab
    lines = material.split('\n')
    for line in lines:
        line = line.strip()
        if not line:
            elements.append(Spacer(1, 6))
            continue
        
        # CORRECT WAY: Use regex to properly replace **text** with <b>text</b>
        import re
        formatted_line = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', line)
        
        # Handle simple Markdown conversion for PDF
        if formatted_line.startswith('# '):
            elements.append(Paragraph(formatted_line[2:], styles['Title']))
        elif formatted_line.startswith('## '):
            elements.append(Paragraph(formatted_line[3:], styles['Heading1']))
        elif formatted_line.startswith('### '):
            elements.append(Paragraph(formatted_line[4:], styles['Heading2']))
        elif formatted_line.startswith('* ') or formatted_line.startswith('- '):
            # Bullet point simulation
            elements.append(Paragraph(f"• {formatted_line[2:]}", styles['Normal']))
        else:
            # Normal text
            elements.append(Paragraph(formatted_line, styles['Normal']))
            elements.append(Spacer(1, 4))

    doc.build(elements)
    buffer.seek(0)
    
    return send_file(buffer, as_attachment=True, download_name=filename, mimetype='application/pdf')

@app.route('/generate_quiz', methods=['POST'])
@login_required
def generate_quiz():
    q_limit = int(request.form.get('question_limit', 5))
    difficulty = request.form.get('difficulty', 'Medium')
    q_type = request.form.get('q_type', 'MCQ')
    learn_first = request.form.get('learn_first') 
    
    session['current_difficulty'] = difficulty
    session['q_type'] = q_type 
    session['duration'] = int(request.form.get('duration', 60))

    quiz_json_text = None
    study_material = None
    
    if 'pdf_file' in request.files and request.files['pdf_file'].filename != '':
        file = request.files['pdf_file']
        pdf_text = extract_text_from_pdf(file)
        if pdf_text:
            session['last_topic'] = f"PDF: {file.filename}"
            if learn_first == 'true':
                study_material = generate_study_guide(source_text=pdf_text)
            else:
                quiz_json_text = generate_quiz_questions(source_text=pdf_text, qcount=q_limit, difficulty=difficulty, q_type=q_type)
    
    elif 'topic' in request.form:
        raw_input = request.form['topic']
        
        if "youtube.com" in raw_input or "youtu.be" in raw_input or "shorts" in raw_input:
            youtube_text = extract_text_from_youtube(raw_input)
            if youtube_text:
                session['last_topic'] = f"YouTube Video Quiz"
                if learn_first == 'true':
                    study_material = generate_study_guide(source_text=youtube_text)
                else:
                    quiz_json_text = generate_quiz_questions(source_text=youtube_text, qcount=q_limit, difficulty=difficulty, q_type=q_type)
            else:
                flash("Could not retrieve captions from this YouTube video. Try another one with subtitles!", "danger")
                return redirect(url_for('dashboard'))
        else:
            session['last_topic'] = raw_input
            if learn_first == 'true':
                study_material = generate_study_guide(topic=raw_input)
            else:
                quiz_json_text = generate_quiz_questions(topic=raw_input, qcount=q_limit, difficulty=difficulty, q_type=q_type)

    if learn_first == 'true' and study_material:
        formatted_material = markdown.markdown(study_material)
        return render_template('study_guide.html', material=formatted_material, raw_material=study_material, topic=session.get('last_topic', 'Study Topic'))

    if not quiz_json_text: return redirect(url_for('dashboard'))

    try:
        questions_data = json.loads(quiz_json_text)
        Question.query.delete()
        for q in questions_data:
            options_val = "||".join(q['options']) if q.get('options') else ""
            db.session.add(Question(text=q['question'], q_type=q_type, options=options_val, correct_answer=q['correct_answer'], explanation=q.get('explanation', '')))
        db.session.commit()
        
        if q_type == 'Flashcard': return redirect(url_for('flashcards'))
        return redirect(url_for('quiz'))
    except Exception as e:
        print(f"Error parsing questions: {e}")
        return redirect(url_for('dashboard'))

@app.route('/flashcards')
@login_required
def flashcards():
    questions = Question.query.all()
    if not questions: return redirect(url_for('dashboard'))
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
    
    update_user_streak(current_user)
    check_achievements(current_user, new_result)
    db.session.commit()
    
    return render_template('result.html', score=score, total=len(questions), results=results, q_type=q_type)

@app.route('/quit')
def quit_quiz(): return redirect(url_for('dashboard'))

@app.template_filter('markdown')
def markdown_filter(text): return markdown.markdown(text or "")

with app.app_context(): db.create_all()

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)