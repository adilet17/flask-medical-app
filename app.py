import os
from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_socketio import SocketIO, join_room, leave_room, send
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename
from datetime import datetime
from dotenv import load_dotenv
import boto3
import logging

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Загрузка переменных окружения
load_dotenv()

# Создание приложения
app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('FLASK_SECRET_KEY', 'your-secret-key')
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # Ограничение размера файла (16MB)
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'sqlite:////persistent/database.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Инициализация базы данных и миграций
db = SQLAlchemy(app)
migrate = Migrate(app, db)
socketio = SocketIO(app, async_mode='eventlet')

# Настройка Amazon S3 для хранения файлов
s3 = boto3.client(
    's3',
    aws_access_key_id=os.getenv('AWS_ACCESS_KEY'),
    aws_secret_access_key=os.getenv('AWS_SECRET_KEY')
)
S3_BUCKET = os.getenv('S3_BUCKET', 'your-bucket-name')

# Модели базы данных
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(120), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    email = db.Column(db.String(120))
    photo = db.Column(db.String(120))
    bio = db.Column(db.Text)

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, nullable=False)
    receiver_id = db.Column(db.Integer, nullable=False)
    message = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.String(50), nullable=False)

class Appointment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, nullable=False)
    doctor_id = db.Column(db.Integer, nullable=False)
    appointment_time = db.Column(db.String(50), nullable=False)

class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=False)
    message = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.String(50), nullable=False)

class Review(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    doctor_id = db.Column(db.Integer, nullable=False)
    patient_id = db.Column(db.Integer, nullable=False)
    rating = db.Column(db.Integer, nullable=False)
    comment = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.String(50), nullable=False)

class Article(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.String(50), nullable=False)

# Создание таблиц и добавление тестовых данных
with app.app_context():
    db.create_all()
    if not User.query.filter_by(username='doctor1').first():
        doctor = User(username='doctor1', password='pass123', role='doctor', email='doctor1@example.com')
        db.session.add(doctor)
    if not User.query.filter_by(username='admin').first():
        admin = User(username='admin', password='admin123', role='admin', email='admin@example.com')
        db.session.add(admin)
    if not Article.query.first():
        article = Article(title='Советы по здоровью', content='Пейте больше воды и спите 8 часов.', timestamp='2025-05-20 12:00:00')
        db.session.add(article)
    db.session.commit()

# Вспомогательные функции
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'png', 'jpg', 'jpeg', 'gif'}

# Маршруты
@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        role = request.form['role']
        email = request.form['email']
        user = User(username=username, password=password, role=role, email=email)
        try:
            db.session.add(user)
            db.session.commit()
            flash('Регистрация успешна! Войдите в систему.')
            return redirect(url_for('login'))
        except Exception as e:
            logger.error(f"Ошибка при регистрации: {e}")
            flash('Имя пользователя уже занято!')
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username, password=password).first()
        if user:
            session['user_id'] = user.id
            session['role'] = user.role
            return redirect(url_for('index'))
        flash('Неверное имя пользователя или пароль!')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    session.pop('role', None)
    return redirect(url_for('login'))

@app.route('/profile', methods=['GET', 'POST'])
def profile():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = User.query.get(session['user_id'])
    if request.method == 'POST':
        user.email = request.form['email']
        user.bio = request.form['bio']
        if 'photo' in request.files:
            file = request.files['photo']
            if file and allowed_file(file.filename):
                try:
                    filename = secure_filename(file.filename)
                    s3.upload_fileobj(file, S3_BUCKET, filename)
                    user.photo = f"https://{S3_BUCKET}.s3.amazonaws.com/{filename}"
                    logger.info(f"Файл {filename} успешно загружен в S3")
                except Exception as e:
                    logger.error(f"Ошибка загрузки файла в S3: {e}")
                    flash('Ошибка при загрузке фотографии!')
        db.session.commit()
        flash('Профиль обновлен!')
        return redirect(url_for('profile'))
    filled_fields = sum(1 for field in [user.username, user.email, user.photo, user.bio] if field)
    profile_completion = (filled_fields / 4) * 100
    return render_template('profile.html', user=user, profile_completion=profile_completion)

@app.route('/appointments')
def appointments():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    appointments = Appointment.query.filter_by(patient_id=session['user_id']).all()
    doctors = {user.id: user.username for user in User.query.filter_by(role='doctor').all()}
    return render_template('appointments.html', appointments=appointments, doctors=doctors)

@app.route('/notifications')
def notifications():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    notifications = Notification.query.filter_by(user_id=session['user_id']).order_by(Notification.timestamp.desc()).all()
    return render_template('notifications.html', notifications=notifications)

@app.route('/doctors')
def doctors():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    doctors = User.query.filter_by(role='doctor').all()
    reviews = Review.query.all()
    doctor_ratings = {}
    for doctor in doctors:
        doctor_reviews = [r.rating for r in reviews if r.doctor_id == doctor.id]
        doctor_ratings[doctor.id] = sum(doctor_reviews) / len(doctor_reviews) if doctor_reviews else 0
    return render_template('doctors.html', doctors=doctors, doctor_ratings=doctor_ratings)

@app.route('/patients')
def patients():
    if 'user_id' not in session or session['role'] != 'doctor':
        return redirect(url_for('login'))
    appointments = Appointment.query.filter_by(doctor_id=session['user_id']).all()
    patient_ids = set(a.patient_id for a in appointments)
    patients = User.query.filter(User.id.in_(patient_ids), User.role == 'patient').all()
    return render_template('patients.html', patients=patients)

@app.route('/reviews/<int:doctor_id>', methods=['GET', 'POST'])
def reviews(doctor_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    if request.method == 'POST':
        rating = int(request.form['rating'])
        comment = request.form['comment']
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        review = Review(doctor_id=doctor_id, patient_id=session['user_id'], rating=rating, comment=comment, timestamp=timestamp)
        db.session.add(review)
        db.session.commit()
        flash('Отзыв добавлен!')
        return redirect(url_for('doctors'))
    doctor = User.query.get(doctor_id)
    reviews = Review.query.filter_by(doctor_id=doctor_id).all()
    return render_template('reviews.html', doctor_id=doctor_id, doctor_name=doctor.username, reviews=reviews)

@app.route('/chat/<int:doctor_id>')
def chat(doctor_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    doctor = User.query.get(doctor_id)
    messages = Message.query.filter(
        ((Message.sender_id == session['user_id']) & (Message.receiver_id == doctor_id)) |
        ((Message.sender_id == doctor_id) & (Message.receiver_id == session['user_id']))
    ).all()
    return render_template('chat.html', doctor_id=doctor_id, doctor_name=doctor.username, messages=messages)

@app.route('/appoint/<int:doctor_id>', methods=['GET', 'POST'])
def appoint(doctor_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    if request.method == 'POST':
        appointment_time = request.form['appointment_time']
        appointment = Appointment(patient_id=session['user_id'], doctor_id=doctor_id, appointment_time=appointment_time)
        notification = Notification(user_id=session['user_id'], message=f"Встреча назначена на {appointment_time}", timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        db.session.add(appointment)
        db.session.add(notification)
        db.session.commit()
        flash('Встреча назначена!')
        return redirect(url_for('doctors'))
    return render_template('appoint.html', doctor_id=doctor_id)

@app.route('/calendar/<int:doctor_id>')
def calendar(doctor_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    doctor = User.query.get(doctor_id)
    appointments = Appointment.query.filter_by(doctor_id=doctor_id).all()
    return render_template('calendar.html', doctor_id=doctor_id, doctor_name=doctor.username, appointments=appointments)

@app.route('/faq')
def faq():
    faqs = [
        ("Как записаться на прием?", "Выберите доктора и используйте форму назначения встречи."),
        ("Могу ли я оставить отзыв?", "Да, после визита вы можете оставить отзыв на странице доктора.")
    ]
    return render_template('faq.html', faqs=faqs)

@app.route('/articles')
def articles():
    articles = Article.query.order_by(Article.timestamp.desc()).all()
    return render_template('articles.html', articles=articles)

@app.route('/support', methods=['GET', 'POST'])
def support():
    if request.method == 'POST':
        message = request.form['message']
        flash('Ваше сообщение отправлено! Мы ответим в ближайшее время.')
        return redirect(url_for('support'))
    return render_template('support.html')

@app.route('/admin')
def admin():
    if 'user_id' not in session or session['role'] != 'admin':
        return redirect(url_for('login'))
    users = User.query.all()
    appointments = Appointment.query.all()
    articles = Article.query.all()
    reviews = Review.query.all()
    return render_template('admin.html', users=users, appointments=appointments, articles=articles, reviews=reviews)

@app.route('/admin/add_article', methods=['GET', 'POST'])
def add_article():
    if 'user_id' not in session or session['role'] != 'admin':
        return redirect(url_for('login'))
    if request.method == 'POST':
        title = request.form['title']
        content = request.form['content']
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        article = Article(title=title, content=content, timestamp=timestamp)
        db.session.add(article)
        db.session.commit()
        flash('Статья добавлена!')
        return redirect(url_for('admin'))
    return render_template('add_article.html')

@app.route('/admin/delete_user/<int:user_id>')
def delete_user(user_id):
    if 'user_id' not in session or session['role'] != 'admin':
        return redirect(url_for('login'))
    user = User.query.get(user_id)
    db.session.delete(user)
    db.session.commit()
    flash('Пользователь удален!')
    return redirect(url_for('admin'))

@app.route('/admin/delete_article/<int:article_id>')
def delete_article(article_id):
    if 'user_id' not in session or session['role'] != 'admin':
        return redirect(url_for('login'))
    article = Article.query.get(article_id)
    db.session.delete(article)
    db.session.commit()
    flash('Статья удалена!')
    return redirect(url_for('admin'))

@app.route('/admin/delete_review/<int:review_id>')
def delete_review(review_id):
    if 'user_id' not in session or session['role'] != 'admin':
        return redirect(url_for('login'))
    review = Review.query.get(review_id)
    db.session.delete(review)
    db.session.commit()
    flash('Отзыв удален!')
    return redirect(url_for('admin'))

@app.route('/analytics')
def analytics():
    if 'user_id' not in session or session['role'] != 'admin':
        return redirect(url_for('login'))
    total_appointments = Appointment.query.count()
    total_patients = User.query.filter_by(role='patient').count()
    total_doctors = User.query.filter_by(role='doctor').count()
    return render_template('analytics.html', total_appointments=total_appointments,
                          total_patients=total_patients, total_doctors=total_doctors)

# WebSocket для чата
@socketio.on('join')
def on_join(data):
    room = data['room']
    join_room(room)
    send({'msg': 'Пользователь вошел в чат'}, to=room)

@socketio.on('message')
def handle_message(data):
    room = data['room']
    message = data['msg']
    sender_id = session['user_id']
    receiver_id = data['receiver_id']
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    msg = Message(sender_id=sender_id, receiver_id=receiver_id, message=message, timestamp=timestamp)
    db.session.add(msg)
    db.session.commit()
    send({'msg': message, 'sender_id': sender_id, 'timestamp': timestamp}, to=room)

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=int(os.getenv('PORT', 5000)))