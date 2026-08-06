1. cd bharatgen-ibm-yojaka-llm-board/qgen_project
2. sudo docker compose -f docker-compose.dev.yml build
3. sudo docker compose -f docker-compose.dev.yml up -d
4. sudo docker compose -f qgen_project/docker-compose.dev.yml exec web python manage.py shell
5. Steps to create super user
   from apps.core.models import User
   u = User.objects.create(username="admin")
   u.role = User.Role.SUPERUSER
   u.set_password('admin123')
   u.is_staff = True
   u.is_superuser = True
   u.role = User.Role.SUPERUSER
   u.is_active_member = True
   u.save()

