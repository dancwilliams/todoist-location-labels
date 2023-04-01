# todoist-location-labels
Todoist service for adding location reminder for specific label


## First setup
The database tables need to be created so run a flask shell:
	
	flask --app app shell

and run the following commands:
	
	from app import db
	db.create_all()
	exit()

This creates the DB and makes it that you are ready for development(or production)
