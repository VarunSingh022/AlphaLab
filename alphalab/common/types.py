"""Shared type aliases."""

from collections.abc import Mapping

type MetadataValue = str | int | float | bool | None
type MetadataMapping = Mapping[str, MetadataValue]

type ParamValue = str | int | float | bool
"""One hyperparameter or configuration value.

Defined here because two packages need the same alias and had it twice:
``alphalab.experiment_tracking.tracker`` (the parameters a run was started
with) and ``alphalab.model_registry.registry`` (the parameters a model was
trained with). They were identical, and the registry's own docstring said so.
A run's parameters and the parameters recorded on the model that run produced
are the same values; they now have one definition, and both modules re-export
this name unchanged.

``MetadataValue`` is deliberately *not* the same alias: metadata admits
``None``, a parameter does not. "The learning rate was not set" and "the
learning rate was ``None``" are different statements, and only the first is
expressible by leaving the key out.
"""
