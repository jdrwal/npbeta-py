"""Forms for create/action views."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any, cast

from django import forms
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import MailSettings, User
from apps.core.models import (
    AdminFee,
    AdminFeePrice,
    Contract,
    EmailTemplate,
    Flat,
    LedgerEntry,
    MeterDefinition,
    MeterPrice,
    MeterReading,
    Room,
    WishlistItem,
)

_DATE = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


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


class FlatScopedSelect(forms.Select):
    """Select whose options carry a data-flat attribute so the client can filter
    them down to the chosen flat (used for rooms and contracts)."""

    def create_option(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        option = super().create_option(*args, **kwargs)
        instance = getattr(option["value"], "instance", None)
        if instance is not None:
            option["attrs"]["data-flat"] = str(instance.flat_id)
        return option


class LedgerEntryForm(forms.ModelForm):
    # Billing month is a plain month picker (YYYY-MM) stored as first-of-month.
    billing_period = forms.DateField(
        label="Miesiąc rozliczeniowy",
        help_text="Miesiąc, którego dotyczy wpłata — niezależny od daty wpłaty.",
        widget=forms.DateInput(attrs={"type": "month"}, format="%Y-%m"),
        input_formats=["%Y-%m", "%Y-%m-%d"],
    )

    class Meta:
        model = LedgerEntry
        fields = [
            "flat",
            "room",
            "contract",
            "kind",
            "short_desc",
            "notes",
            "record_date",
            "billing_period",
            "amount_in_taxable",
            "is_mortgage",
        ]
        widgets = {
            "record_date": _DATE,
            "room": FlatScopedSelect,
            "contract": FlatScopedSelect,
        }
        labels = {
            "flat": "Mieszkanie",
            "room": "Pokój",
            "contract": "Umowa",
            "kind": "Rodzaj",
            "short_desc": "Opis",
            "notes": "Notatki",
            "record_date": "Data wpłaty",
            "amount_in_taxable": "Kwota",
            "is_mortgage": "Kredyt",
        }

    def __init__(self, *args: Any, user: User | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        today = timezone.now().date()
        self.fields["record_date"].initial = today
        self.fields["billing_period"].initial = today.replace(day=1)
        if user is not None:
            _scope(self, "flat", Flat.objects.filter(owner=user))
            _scope(self, "room", Room.objects.filter(owner=user))
            # Only contracts that have not ended yet (active / indefinite).
            _scope(
                self,
                "contract",
                Contract.objects.filter(owner=user).filter(
                    Q(contract_end__gte=today) | Q(contract_end__isnull=True)
                ),
            )

    def clean_billing_period(self) -> Any:
        value = self.cleaned_data.get("billing_period")
        return value.replace(day=1) if value else value


class MeterReadingForm(forms.ModelForm):
    class Meta:
        model = MeterReading
        fields = ["meter", "read_date", "value", "is_estimated"]
        widgets = {"read_date": forms.DateInput(attrs={"type": "date"})}
        labels = {
            "meter": "Licznik",
            "read_date": "Data odczytu",
            "value": "Wartość",
            "is_estimated": "Odczyt szacowany",
        }

    def __init__(self, *args: Any, user: User | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if user is not None:
            field = cast(forms.ModelChoiceField, self.fields["meter"])
            field.queryset = MeterDefinition.objects.filter(
                owner=user
            ).select_related("flat")
            field.label_from_instance = (  # type: ignore[assignment]
                lambda obj: f"{obj.flat.code} · {obj.flat.city} — {obj.name} ({obj.unit})"
            )


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
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        input_formats=["%Y-%m-%d"],
        label="Początek okresu",
    )
    period_end = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        input_formats=["%Y-%m-%d"],
        label="Koniec okresu",
    )
    email_tenants = forms.BooleanField(
        required=False, label="Wyślij rozliczenie do najemców (w tle)"
    )

    def __init__(self, *args: Any, user: User | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if user is not None:
            field = cast(forms.ModelChoiceField, self.fields["flat"])
            field.queryset = Flat.objects.filter(owner=user)
        # Default to the previous (last complete) calendar month.
        first_of_this_month = timezone.localdate().replace(day=1)
        last_month_end = first_of_this_month - timedelta(days=1)
        self.fields["period_start"].initial = last_month_end.replace(day=1)
        self.fields["period_end"].initial = last_month_end

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        start, end = cleaned.get("period_start"), cleaned.get("period_end")
        if start and end and end < start:
            raise forms.ValidationError(
                "Data końca musi być nie wcześniejsza niż data początku."
            )
        if end and end > timezone.localdate():
            raise forms.ValidationError(
                "Okres nie może obejmować przyszłości — rozlicz zakończony miesiąc."
            )
        return cleaned


class MailSettingsForm(forms.ModelForm):
    MODE_CHOICES = [
        ("default", "Konto domyślne serwera"),
        ("custom", "Własne ustawienia SMTP"),
    ]
    mail_mode = forms.ChoiceField(
        choices=MODE_CHOICES,
        widget=forms.RadioSelect,
        label="Sposób wysyłki",
    )

    class Meta:
        model = MailSettings
        fields = [
            "smtp_host",
            "smtp_port",
            "smtp_user",
            "smtp_password",
            "from_email",
            "use_tls",
            "use_ssl",
        ]
        widgets = {
            "smtp_password": forms.PasswordInput(
                render_value=False, attrs={"autocomplete": "new-password"}
            ),
        }
        labels = {
            "smtp_host": "Serwer SMTP",
            "smtp_port": "Port",
            "smtp_user": "Użytkownik (login)",
            "smtp_password": "Hasło",
            "from_email": "Adres nadawcy (From)",
            "use_tls": "Użyj TLS (STARTTLS)",
            "use_ssl": "Użyj SSL",
        }
        help_texts = {
            "smtp_password": "Zostaw puste, aby nie zmieniać zapisanego hasła.",
            "from_email": "Np. najem@twojadomena.pl",
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        use_default = not self.instance.pk or self.instance.use_default
        self.fields["mail_mode"].initial = "default" if use_default else "custom"
        # SMTP fields are only required in custom mode (validated in clean()).
        for name in ("smtp_host", "smtp_port"):
            self.fields[name].required = False

    def clean_smtp_password(self) -> str:
        value = self.cleaned_data.get("smtp_password", "")
        # Blank means "keep the stored password" rather than clearing it.
        if not value and self.instance and self.instance.pk:
            return self.instance.smtp_password
        return value

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        if cleaned.get("mail_mode") == "custom" and not cleaned.get("smtp_host"):
            self.add_error("smtp_host", "Podaj adres serwera SMTP.")
        return cleaned

    def save(self, commit: bool = True) -> MailSettings:
        obj = cast(MailSettings, super().save(commit=False))
        obj.use_default = self.cleaned_data.get("mail_mode") == "default"
        if commit:
            obj.save()
        return obj


class AdminFeeForm(forms.ModelForm):
    class Meta:
        model = AdminFee
        fields = ["title", "is_individual"]
        labels = {
            "title": "Nazwa opłaty",
            "is_individual": "Naliczana indywidualnie (na osobę)",
        }


class AdminFeePriceForm(forms.ModelForm):
    class Meta:
        model = AdminFeePrice
        fields = ["price", "price_date"]
        widgets = {"price_date": _DATE}
        labels = {
            "price": "Kwota (zł / miesiąc)",
            "price_date": "Obowiązuje od",
        }

    def clean_price(self) -> Any:
        price = self.cleaned_data.get("price")
        if price is None:
            raise forms.ValidationError("Podaj kwotę.")
        return price


class MeterFieldsForm(forms.ModelForm):
    """Inline edit of a meter's name/unit on the fees page."""

    class Meta:
        model = MeterDefinition
        fields = ["name", "unit"]
        labels = {"name": "Nazwa", "unit": "Jednostka"}


class MeterPriceForm(forms.ModelForm):
    class Meta:
        model = MeterPrice
        fields = ["price", "price_date"]
        widgets = {"price_date": _DATE}
        labels = {
            "price": "Stawka (zł / jednostkę)",
            "price_date": "Obowiązuje od",
        }

    def clean_price(self) -> Any:
        price = self.cleaned_data.get("price")
        if price is None:
            raise forms.ValidationError("Podaj stawkę.")
        return price


class FeeCreateForm(forms.Form):
    """Unified 'add fee' form for both admin fees and metered utilities."""

    KIND_CHOICES = [
        ("admin", "Opłata stała (administracyjna)"),
        ("kWh", "Licznik — energia elektryczna (kWh)"),
        ("m³", "Licznik — gaz / woda (m³)"),
    ]

    flat = forms.ModelChoiceField(queryset=Flat.objects.none(), label="Mieszkanie")
    kind = forms.ChoiceField(choices=KIND_CHOICES, label="Rodzaj")
    title = forms.CharField(max_length=32, label="Opis / nazwa")
    amount = forms.DecimalField(
        max_digits=12, decimal_places=4, min_value=Decimal(0), label="Wysokość (stawka)"
    )
    is_individual = forms.BooleanField(
        required=False, label="Naliczana indywidualnie (na osobę)"
    )
    price_date = forms.DateField(
        required=False,
        label="Obowiązuje od",
        widget=_DATE,
        input_formats=["%Y-%m-%d"],
    )

    def __init__(self, *args: Any, user: User | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if user is not None:
            cast(forms.ModelChoiceField, self.fields["flat"]).queryset = (
                Flat.objects.filter(owner=user)
            )


class SecuritySettingsForm(forms.ModelForm):
    """Lets the user pick an inactivity logout window between 5 min and 6 h."""

    MIN_MINUTES = 5
    MAX_MINUTES = 360

    class Meta:
        model = User
        fields = ["session_timeout_minutes"]
        widgets = {
            "session_timeout_minutes": forms.NumberInput(
                attrs={
                    "type": "range",
                    "min": 5,
                    "max": 360,
                    "step": 5,
                    "class": "np-slider",
                }
            ),
        }
        labels = {
            "session_timeout_minutes": "Wylogowanie po bezczynności",
        }

    def clean_session_timeout_minutes(self) -> int:
        value = int(self.cleaned_data["session_timeout_minutes"])
        return max(self.MIN_MINUTES, min(self.MAX_MINUTES, value))


class WishlistForm(forms.ModelForm):
    """Lets a user submit a problem report or feature wish from settings."""

    class Meta:
        model = WishlistItem
        fields = ["kind", "subject", "body"]
        widgets = {
            "body": forms.Textarea(attrs={"rows": 4}),
        }
        labels = {
            "kind": "Rodzaj",
            "subject": "Temat",
            "body": "Opis",
        }
        help_texts = {
            "body": "Opisz problem lub życzenie tak dokładnie, jak potrafisz.",
        }


class WishlistReplyForm(forms.Form):
    """A follow-up message the user adds to their own wishlist item."""

    body = forms.CharField(
        label="Odpowiedź",
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Napisz odpowiedź…"}),
    )


class EmailTemplateForm(forms.ModelForm):
    """Create/edit a reusable e-mail template owned by the landlord."""

    class Meta:
        model = EmailTemplate
        fields = ["kind", "name", "subject", "body", "is_active"]
        widgets = {
            "body": forms.Textarea(attrs={"rows": 8}),
        }
        labels = {
            "kind": "Rodzaj",
            "name": "Nazwa",
            "subject": "Temat",
            "body": "Treść",
            "is_active": "Aktywny",
        }


class AdHocEmailForm(forms.Form):
    """Compose an ad-hoc notification to all active tenants of a flat."""

    flat = forms.ModelChoiceField(queryset=Flat.objects.none(), label="Mieszkanie")
    template = forms.ModelChoiceField(
        queryset=EmailTemplate.objects.none(),
        required=False,
        label="Szablon (opcjonalnie)",
    )
    subject = forms.CharField(max_length=200, label="Temat")
    body = forms.CharField(label="Treść", widget=forms.Textarea(attrs={"rows": 8}))

    def __init__(self, *args: Any, user: User | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if user is not None:
            cast(forms.ModelChoiceField, self.fields["flat"]).queryset = (
                Flat.objects.filter(owner=user)
            )
            cast(forms.ModelChoiceField, self.fields["template"]).queryset = (
                EmailTemplate.objects.filter(owner=user)
            )

