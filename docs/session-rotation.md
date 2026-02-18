# Session rotation note

After deploying a new `SECRET_KEY`, all existing Flask sessions become invalid.
Every user must log in again.
