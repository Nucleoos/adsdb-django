from django.db.backends import BaseDatabaseClient

class DatabaseClient(BaseDatabaseClient):
    executable_name = ''

    def runshell(self):
        raise NotImplementedError
