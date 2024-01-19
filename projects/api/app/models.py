# -*- coding: utf-8 -*-
from tortoise import fields
from tortoise.models import Model


class Agent(Model):
    id = fields.UUIDField(pk=True)
    name = fields.CharField(max_length=255, unique=True)
    slug = fields.CharField(max_length=255, unique=True)
    last_heartbeat = fields.DatetimeField(null=True)
    cameras = fields.ManyToManyField("app.Camera", related_name="agents")


class Camera(Model):
    id = fields.CharField(max_length=30, pk=True)
    name = fields.CharField(max_length=255, null=True)
    rtsp_url = fields.CharField(max_length=255, unique=True)
    update_interval = fields.IntField()
    latitude = fields.FloatField()
    longitude = fields.FloatField()


class CameraIdentification(Model):
    id = fields.UUIDField(pk=True)
    camera = fields.ForeignKeyField("app.Camera", related_name="identifications")
    object = fields.ForeignKeyField("app.Object", related_name="identifications")
    timestamp = fields.DatetimeField()
    label = fields.BooleanField()


class Object(Model):
    id = fields.UUIDField(pk=True)
    name = fields.CharField(max_length=255, unique=True)
    slug = fields.CharField(max_length=255, unique=True)


class Prompt(Model):
    id = fields.UUIDField(pk=True)
    name = fields.CharField(max_length=255, unique=True)
    objects = fields.ManyToManyField("app.Object", related_name="prompts")
    prompt_text = fields.TextField()
    max_output_token = fields.IntField()
    temperature = fields.FloatField()
    top_k = fields.IntField()
    top_p = fields.FloatField()
