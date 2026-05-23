FROM tiangolo/uwsgi-nginx-flask:python3.10

COPY ./requirements.txt /app/requirements.txt
RUN python3 -m pip install --no-cache-dir --upgrade -r /app/requirements.txt

ENV LISTEN_PORT=8080
EXPOSE 8700

COPY ./app /app