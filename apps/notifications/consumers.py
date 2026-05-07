import json

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

from django.contrib.auth import get_user_model

from .chat_permissions import can_user_message
from .models import Message

User = get_user_model()


class NotificationConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        user = self.scope.get("user")
        if not user or user.is_anonymous:
            await self.close()
            return

        self.group_name = f"user_{user.id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def send_notification(self, event):
        await self.send(text_data=json.dumps(event.get("data", {})))


class ChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        user = self.scope.get("user")
        if not user or user.is_anonymous:
            await self.close(code=4401)
            return

        route_user_id = self.scope["url_route"]["kwargs"].get("user_id")
        if str(user.id) != str(route_user_id):
            await self.close(code=4403)
            return

        self.group_name = f"user_chat_{user.id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive(self, text_data=None, bytes_data=None):
        user = self.scope.get("user")
        if not user or user.is_anonymous:
            await self.send(text_data=json.dumps({"error": "Authentication required."}))
            return
        try:
            payload = json.loads(text_data or "{}")
        except json.JSONDecodeError:
            await self.send(text_data=json.dumps({"error": "Invalid JSON payload."}))
            return

        receiver_id = payload.get("receiver_id")
        content = (payload.get("content") or "").strip()
        if not receiver_id or not content:
            await self.send(text_data=json.dumps({"error": "receiver_id and content are required."}))
            return

        receiver = await sync_to_async(User.objects.filter(id=receiver_id).first)()
        if not receiver:
            await self.send(text_data=json.dumps({"error": "Receiver not found."}))
            return
        is_allowed = await sync_to_async(can_user_message)(user, receiver)
        if not is_allowed:
            await self.send(text_data=json.dumps({"error": "You are not allowed to message this user."}))
            return

        msg = await sync_to_async(Message.objects.create)(
            school=user.school,
            sender=user,
            receiver=receiver,
            content=content,
        )
        event = {
            "type": "chat_message",
            "message_id": msg.id,
            "sender_id": user.id,
            "sender_name": user.get_full_name() or user.username,
            "receiver_id": receiver.id,
            "content": msg.content,
            "timestamp": msg.timestamp.isoformat(),
            "is_read": False,
        }
        await self.channel_layer.group_send(f"user_chat_{user.id}", {"type": "chat.message", "data": event})
        await self.channel_layer.group_send(f"user_chat_{receiver.id}", {"type": "chat.message", "data": event})

    async def chat_message(self, event):
        await self.send(text_data=json.dumps(event.get("data", {})))

