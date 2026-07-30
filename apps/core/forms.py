"""Forms for create/action views."""

from __future__ import annotations

from typing import Any, cast

from django import forms

from apps.accounts.models import User
from apps.core.models import (
    Contract,
    Flat,
    LedgerEntry,
    MeterDefinition,
    MeterReading,
    Room,
)

_DATE = forms.DateInput(attrs={"type": "date"})


def _scope(form: forms.ModelForm, field: str, queryset: Any) -> None:
    cast(forms.ModelChoiceField, form.fields[field]).queryset = queryset


class FlatForm(forms.ModelForm):
    class Meta:
        model = Flat
        fields = [
            "city",
            "street",
            "building_no",
            "flat_no",
            "flat_size",
            "code",
            "room_count",
            "color",
        ]

    def __init__(self, *args: Any, user: User | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)


class RoomForm(forms.ModelForm):
    class Meta:
        model = Room
        fields = ["flat", "room_no", "name", "size", "beds", "fee", "deposit"]

    def __init__(self, *args: Any, user: User | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if user is not None:
            _scope(self, "flat", Flat.objects.filter(owner=user))


class ContractForm(forms.ModelForm):
    class Meta:
        model = Contract
        fields = [
            "flat",
            "room",
            "contract_number",
            "tenant_name",
            "email",
            "phone",
            "price",
            "deposit",
            "contract_date",
            "contract_start",
            "contract_end",
            "payment_day",
        ]
        widgets = {
            "contract_date": _DATE,
            "contract_start": _DATE,
            "contract_end": _DATE,
        }

    def __init__(self, *args: Any, user: User | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if user is not None:
            _scope(self, "flat", Flat.objects.filter(owner=user))
            _scope(self, "room", Room.objects.filter(owner=user))


class LedgerEntryForm(forms.ModelForm):
    class Meta:
        model = LedgerEntry
        fields = [
            "flat",
            "room",
            "contract",
            "short_desc",
            "notes",
            "record_date",
            "amount_in_taxable",
            "amount_in_not_taxable",
            "amount_out",
            "cost",
            "is_mortgage",
        ]
        widgets = {"record_date": _DATE}

    def __init__(self, *args: Any, user: User | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if user is not None:
            _scope(self, "flat", Flat.objects.filter(owner=user))
            _scope(self, "room", Room.objects.filter(owner=user))
            _scope(self, "contract", Contract.objects.filter(owner=user))


class MeterReadingForm(forms.ModelForm):
    class Meta:
        model = MeterReading
        fields = ["meter", "read_date", "value"]
        widgets = {"read_date": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args: Any, user: User | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if user is not None:
            field = cast(forms.ModelChoiceField, self.fields["meter"])
            field.queryset = MeterDefinition.objects.filter(owner=user)


class SettlementForm(forms.Form):
    flat = forms.ModelChoiceField(queryset=Flat.objects.none())
    period_start = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    period_end = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))

    def __init__(self, *args: Any, user: User | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if user is not None:
            field = cast(forms.ModelChoiceField, self.fields["flat"])
            field.queryset = Flat.objects.filter(owner=user)

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        start, end = cleaned.get("period_start"), cleaned.get("period_end")
        if start and end and end < start:
            raise forms.ValidationError("End date must be on or after the start date.")
        return cleaned
