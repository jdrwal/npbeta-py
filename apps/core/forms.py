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
        labels = {
            "city": "Miasto",
            "street": "Ulica",
            "building_no": "Nr budynku",
            "flat_no": "Nr mieszkania",
            "flat_size": "Powierzchnia (m²)",
            "code": "Kod",
            "room_count": "Liczba pokoi",
            "color": "Kolor",
        }

    def __init__(self, *args: Any, user: User | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)


class RoomForm(forms.ModelForm):
    class Meta:
        model = Room
        fields = ["flat", "room_no", "name", "size", "beds", "fee", "deposit"]
        labels = {
            "flat": "Mieszkanie",
            "room_no": "Nr pokoju",
            "name": "Nazwa",
            "size": "Powierzchnia (m²)",
            "beds": "Liczba miejsc",
            "fee": "Czynsz",
            "deposit": "Kaucja",
        }

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
        labels = {
            "flat": "Mieszkanie",
            "room": "Pokój",
            "contract_number": "Numer umowy",
            "tenant_name": "Najemca",
            "email": "E-mail",
            "phone": "Telefon",
            "price": "Czynsz",
            "deposit": "Kaucja",
            "contract_date": "Data umowy",
            "contract_start": "Początek najmu",
            "contract_end": "Koniec najmu",
            "payment_day": "Dzień płatności",
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
        labels = {
            "flat": "Mieszkanie",
            "room": "Pokój",
            "contract": "Umowa",
            "short_desc": "Opis",
            "notes": "Notatki",
            "record_date": "Data",
            "amount_in_taxable": "Przychód opodatkowany",
            "amount_in_not_taxable": "Przychód nieopodatkowany",
            "amount_out": "Wydatek",
            "cost": "Koszt",
            "is_mortgage": "Kredyt",
        }

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
        labels = {
            "meter": "Licznik",
            "read_date": "Data odczytu",
            "value": "Wartość",
        }

    def __init__(self, *args: Any, user: User | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if user is not None:
            field = cast(forms.ModelChoiceField, self.fields["meter"])
            field.queryset = MeterDefinition.objects.filter(owner=user)


class MeterDefinitionForm(forms.ModelForm):
    class Meta:
        model = MeterDefinition
        fields = ["flat", "name", "unit"]
        labels = {
            "flat": "Mieszkanie",
            "name": "Nazwa",
            "unit": "Jednostka",
        }

    def __init__(self, *args: Any, user: User | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if user is not None:
            _scope(self, "flat", Flat.objects.filter(owner=user))


class SettlementForm(forms.Form):
    flat = forms.ModelChoiceField(queryset=Flat.objects.none(), label="Mieszkanie")
    period_start = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date"}), label="Początek okresu"
    )
    period_end = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date"}), label="Koniec okresu"
    )
    email_tenants = forms.BooleanField(
        required=False, label="Wyślij rozliczenie do najemców (w tle)"
    )

    def __init__(self, *args: Any, user: User | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if user is not None:
            field = cast(forms.ModelChoiceField, self.fields["flat"])
            field.queryset = Flat.objects.filter(owner=user)

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        start, end = cleaned.get("period_start"), cleaned.get("period_end")
        if start and end and end < start:
            raise forms.ValidationError("Data końca musi być nie wcześniejsza niż data początku.")
        return cleaned
