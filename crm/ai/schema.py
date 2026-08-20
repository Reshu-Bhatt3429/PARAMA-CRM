"""A small JSON-schema check, so "the model answered JSON" stops meaning "fine".

Why this exists
---------------
`crm.ai.client.complete` asks the provider for an object "matching this schema"
and, until now, checked only that the answer parsed and was an object. A model
that returned `{}`, or `{"1": null}`, or an object with the right keys holding
lists instead of strings, got through -- and the caller then wrote whatever that
was into a WhatsApp template variable or an itinerary day.

A model that answers wrongly is not an edge case. It is the normal failure of
the thing, and the client already knows how to handle it: one retry that tells
the model what was wrong, then a fall back to deterministic values. That
recovery only runs if something notices, which is what this module does.

Why not `jsonschema`
--------------------
The app takes no new Python dependency (master spec C1/C2), and the schemas this
app sends are small and hand-written. This validator covers exactly what they
use and refuses what it does not understand rather than passing it silently:

    type (single or list), properties, required, additionalProperties,
    items, enum, const, minimum, maximum, minLength, maxLength,
    minItems, maxItems, pattern, anyOf, oneOf, allOf, nullable

Anything else in a schema raises `UnsupportedSchema`, which is a programming
error in the caller and not something a model can trigger. That way an author
who writes a `$ref` finds out at once instead of getting a validator that quietly
approves everything.
"""

import re

SUPPORTED_KEYWORDS = frozenset(
	{
		"type",
		"properties",
		"required",
		"additionalProperties",
		"items",
		"enum",
		"const",
		"minimum",
		"maximum",
		"minLength",
		"maxLength",
		"minItems",
		"maxItems",
		"pattern",
		"anyOf",
		"oneOf",
		"allOf",
		"nullable",
		# Documentation only. They describe the field to the model and constrain
		# nothing, so they are accepted and ignored.
		"title",
		"description",
		"examples",
		"default",
	}
)

TYPE_CHECKS = {
	"object": lambda value: isinstance(value, dict),
	"array": lambda value: isinstance(value, list),
	"string": lambda value: isinstance(value, str),
	# `bool` is a subclass of `int` in Python. True is not a number here.
	"number": lambda value: isinstance(value, int | float) and not isinstance(value, bool),
	"integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
	"boolean": lambda value: isinstance(value, bool),
	"null": lambda value: value is None,
}


class SchemaViolation(ValueError):
	"""The answer does not match the schema. The message names the path."""


class UnsupportedSchema(ValueError):
	"""The schema uses a keyword this validator does not implement."""


def validate(instance, schema: dict, path: str = "") -> None:
	"""Raise `SchemaViolation` unless `instance` matches `schema`.

	`path` is threaded through so the message says which member is wrong --
	`items[0].title`, not "the answer". A retry prompt that names the fault is
	the one the model can act on.
	"""
	if not isinstance(schema, dict):
		raise UnsupportedSchema("a schema must be an object")

	unknown = set(schema) - SUPPORTED_KEYWORDS
	if unknown:
		raise UnsupportedSchema(f"unsupported schema keyword(s): {', '.join(sorted(unknown))}")

	if instance is None and schema.get("nullable"):
		return

	check_type(instance, schema, path)
	check_enum(instance, schema, path)
	check_combinators(instance, schema, path)
	check_number(instance, schema, path)
	check_string(instance, schema, path)
	check_array(instance, schema, path)
	check_object(instance, schema, path)


def fail(path: str, message: str):
	raise SchemaViolation(f"{where(path)} {message}")


def where(path: str) -> str:
	return f"`{path}`" if path else "the answer"


def child(path: str, part) -> str:
	if isinstance(part, int):
		return f"{path}[{part}]"
	return f"{path}.{part}" if path else str(part)


# --- one keyword each ------------------------------------------------------


def check_type(instance, schema: dict, path: str) -> None:
	declared = schema.get("type")
	if declared is None:
		return

	names = declared if isinstance(declared, list) else [declared]
	for name in names:
		check = TYPE_CHECKS.get(name)
		if check is None:
			raise UnsupportedSchema(f"unsupported type {name!r}")
		if check(instance):
			return

	fail(path, f"should be {' or '.join(names)}, not {type(instance).__name__}")


def check_enum(instance, schema: dict, path: str) -> None:
	if "enum" in schema and instance not in schema["enum"]:
		fail(path, f"should be one of {schema['enum']}")

	if "const" in schema and instance != schema["const"]:
		fail(path, f"should be {schema['const']!r}")


def check_combinators(instance, schema: dict, path: str) -> None:
	if "allOf" in schema:
		for option in schema["allOf"]:
			validate(instance, option, path)

	if "anyOf" in schema and not any(matches(instance, option, path) for option in schema["anyOf"]):
		fail(path, "matches none of the allowed shapes")

	if "oneOf" in schema:
		matched = sum(1 for option in schema["oneOf"] if matches(instance, option, path))
		if matched != 1:
			fail(path, f"should match exactly one of the allowed shapes, matched {matched}")


def matches(instance, schema: dict, path: str) -> bool:
	try:
		validate(instance, schema, path)
	except SchemaViolation:
		return False
	return True


def check_number(instance, schema: dict, path: str) -> None:
	if not isinstance(instance, int | float) or isinstance(instance, bool):
		return

	minimum = schema.get("minimum")
	if minimum is not None and instance < minimum:
		fail(path, f"should be at least {minimum}")

	maximum = schema.get("maximum")
	if maximum is not None and instance > maximum:
		fail(path, f"should be at most {maximum}")


def check_string(instance, schema: dict, path: str) -> None:
	if not isinstance(instance, str):
		return

	minimum = schema.get("minLength")
	if minimum is not None and len(instance) < minimum:
		fail(path, f"should be at least {minimum} characters")

	maximum = schema.get("maxLength")
	if maximum is not None and len(instance) > maximum:
		fail(path, f"should be at most {maximum} characters")

	pattern = schema.get("pattern")
	if pattern is not None and not re.search(pattern, instance):
		fail(path, f"should match {pattern}")


def check_array(instance, schema: dict, path: str) -> None:
	if not isinstance(instance, list):
		return

	minimum = schema.get("minItems")
	if minimum is not None and len(instance) < minimum:
		fail(path, f"should hold at least {minimum} items")

	maximum = schema.get("maxItems")
	if maximum is not None and len(instance) > maximum:
		fail(path, f"should hold at most {maximum} items")

	item_schema = schema.get("items")
	if isinstance(item_schema, dict):
		for index, item in enumerate(instance):
			validate(item, item_schema, child(path, index))


def check_object(instance, schema: dict, path: str) -> None:
	if not isinstance(instance, dict):
		return

	properties = schema.get("properties") or {}

	for key in schema.get("required") or []:
		if key not in instance:
			fail(path, f"is missing {key!r}")

	extra = schema.get("additionalProperties", True)
	if extra is False:
		unexpected = sorted(set(instance) - set(properties))
		if unexpected:
			fail(path, f"has unexpected member(s) {', '.join(unexpected)}")

	for key, value in instance.items():
		if key in properties:
			validate(value, properties[key], child(path, key))
		elif isinstance(extra, dict):
			validate(value, extra, child(path, key))
