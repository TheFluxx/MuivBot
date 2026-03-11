from environs import Env

env = Env()

env.read_env()

BOT_TOKEN = env.str('TOKEN')

DB_DRIVER = env.str('DB_DRIVER')
DB_USERNAME = env.str('DB_USERNAME')
DB_PASSWORD = env.str('DB_PASSWORD')
DB_HOST = env.str('DB_HOST')
DB_NAME = env.str('DB_NAME')

ADMIN_LOGIN = env.str('ADMIN_LOGIN', '')
ADMIN_PASSWORD = env.str('ADMIN_PASSWORD', '')
STAROSTA_LOGIN = env.str('STAROSTA_LOGIN', '')
STAROSTA_PASSWORD = env.str('STAROSTA_PASSWORD', '')
TEACHER_LOGIN = env.str('TEACHER_LOGIN', '')
TEACHER_PASSWORD = env.str('TEACHER_PASSWORD', '')



