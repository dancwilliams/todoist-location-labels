# start by pulling the python image
FROM python:3.11-alpine

# copy the requirements file into the image
COPY ./requirements.txt /app/requirements.txt

# switch working directory
WORKDIR /app

# install the dependencies and packages in the requirements file
RUN apk add --no-cache g++ gcc libxslt-dev musl-dev linux-headers python3-dev
RUN pip install honeycomb-opentelemetry --pre
RUN pip install -r requirements.txt
# RUN python -m pip install honeycomb-opentelemetry --pre
# RUN opentelemetry-bootstrap --action=install

# copy every content from the local file to the image
COPY . /app

# configure the container to run in an executed manner
# ENTRYPOINT [ "python" ]

ENTRYPOINT [ "opentelemetry-instrument", "gunicorn", "-b [::]:5000", "app:app" ]

# CMD [ "" ]
