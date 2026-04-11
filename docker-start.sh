python -m venv venv
source venv/Scripts/activate
pip install uv
uv pip install -r requirements.txt
docker-compose up --build