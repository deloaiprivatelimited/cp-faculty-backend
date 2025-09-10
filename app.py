import os
from flask import Flask
from mongoengine import connect
from dotenv import load_dotenv
from flask_cors import CORS
from routes.college_admin import collegeadmin_bp
from routes.student_routes import bp
from routes.test_mail import test_mail
from routes.test.tests import test_bp
from routes.test.section import test_bp as section_bp
from routes.test.questions.mcq import mcq_bp as test_mcq_bp
# Load environment variables
load_dotenv()

def create_app():
    app = Flask(__name__)

    # Flask config
    CORS(app, resources={r"/*": {"origins": "*"}})
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "fallback-secret")

    # Connect to MongoDB Atlas
    connect(host=os.getenv("MONGO_URI"))
    app.register_blueprint(collegeadmin_bp)
    app.register_blueprint(bp)
    app.register_blueprint(test_mail)
    app.register_blueprint(test_bp)
    app.register_blueprint(section_bp)
    app.register_blueprint(test_mcq_bp)
    @app.route("/")
    def home():
        return {"message": "CP Admin API is running 🚀"}

    return app

if __name__ == "__main__":
    app = create_app()
    app.run(debug=True, port=8000)  # <-- change port here

