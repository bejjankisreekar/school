"""

Payroll models. Tenant-schema (school_data). Teacher from school_data.

"""

from decimal import Decimal



from django.core.exceptions import ValidationError

from django.db import models





class SalaryComponent(models.Model):

    class ComponentType(models.TextChoices):

        ALLOWANCE = "ALLOWANCE", "Allowance"

        DEDUCTION = "DEDUCTION", "Deduction"



    class CalculationType(models.TextChoices):

        PERCENTAGE = "PERCENTAGE", "Percentage of Basic"

        FIXED = "FIXED", "Fixed Amount"



    name = models.CharField(max_length=100)

    code = models.CharField(

        max_length=40,

        blank=True,

        help_text="Short label for reports (e.g. HRA, PF). Optional.",

    )

    description = models.TextField(

        blank=True,

        help_text="Internal notes: policy, eligibility, or accounting mapping.",

    )

    component_type = models.CharField(max_length=20, choices=ComponentType.choices)

    calculation_type = models.CharField(max_length=20, choices=CalculationType.choices)

    value = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text="% or fixed amount")

    is_active = models.BooleanField(default=True)

    order = models.PositiveIntegerField(

        default=1,

        help_text="1 = first in lists; lower numbers appear before higher for the same category.",

    )



    class Meta:

        ordering = ["component_type", "order", "name"]



    def __str__(self):

        return self.name



    def calculate(self, basic_salary):

        return self.compute_amount(basic_salary, self.calculation_type, self.value)



    @classmethod

    def compute_amount(cls, basic_salary, calculation_type, value):

        v = value if value is not None else Decimal("0")

        if calculation_type == cls.CalculationType.PERCENTAGE:

            return basic_salary * (v / Decimal("100"))

        return v





class SalaryStructure(models.Model):

    teacher = models.OneToOneField(

        "school_data.Teacher",

        on_delete=models.CASCADE,

        related_name="salary_structure",

    )

    designation = models.CharField(max_length=100, blank=True)

    department = models.CharField(max_length=100, blank=True)

    basic_salary = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    use_default_salary_components = models.BooleanField(

        default=True,

        help_text="If True, every active allowance and deduction applies. If False, only linked components apply.",

    )

    applicable_components = models.ManyToManyField(

        "SalaryComponent",

        through="SalaryStructureComponent",

        blank=True,

        related_name="salary_structures",

        help_text="Links when not using default list, plus optional per-employee rate overrides.",

    )



    class Meta:

        ordering = ["teacher__user__first_name", "teacher__user__last_name"]



    def __str__(self):

        return f"{self.teacher} - {self.designation or 'N/A'}"



    def applicable_allowances(self):

        qs = SalaryComponent.objects.filter(

            component_type=SalaryComponent.ComponentType.ALLOWANCE,

            is_active=True,

        ).order_by("order", "name")

        if not self.pk or self.use_default_salary_components:

            return qs

        chosen = set(self.component_links.values_list("component_id", flat=True))

        return qs.filter(pk__in=chosen) if chosen else qs.none()



    def applicable_deductions(self):

        qs = SalaryComponent.objects.filter(

            component_type=SalaryComponent.ComponentType.DEDUCTION,

            is_active=True,

        ).order_by("order", "name")

        if not self.pk or self.use_default_salary_components:

            return qs

        chosen = set(self.component_links.values_list("component_id", flat=True))

        return qs.filter(pk__in=chosen) if chosen else qs.none()



    def resolve_component_terms(self, component):

        """(calculation_type, value) for this head on this employee."""

        if not self.pk:

            return component.calculation_type, component.value

        link = self.component_links.filter(component_id=component.pk).first()

        if link and not link.use_component_default:

            return link.override_calculation_type, link.override_value or Decimal("0")

        return component.calculation_type, component.value



    def amount_for_component(self, component, basic=None):

        basic = basic if basic is not None else self.basic_salary

        ct, val = self.resolve_component_terms(component)

        return SalaryComponent.compute_amount(basic, ct, val)



    def total_allowances(self):

        total = Decimal("0")

        for c in self.applicable_allowances():

            total += self.amount_for_component(c)

        return total



    def total_deductions(self, advance_deduction=Decimal("0")):

        total = Decimal("0")

        for c in self.applicable_deductions():

            total += self.amount_for_component(c)

        return total + advance_deduction



    def net_salary(self, advance_deduction=Decimal("0")):

        return self.basic_salary + self.total_allowances() - self.total_deductions(advance_deduction)



    def uses_custom_components(self):

        if not self.pk:

            return False

        if not self.use_default_salary_components:

            return True

        return self.component_links.filter(use_component_default=False).exists()





class SalaryStructureComponent(models.Model):

    """Per-employee link to a salary head; optional % or fixed override."""



    salary_structure = models.ForeignKey(

        SalaryStructure,

        on_delete=models.CASCADE,

        related_name="component_links",

        db_column="salarystructure_id",

    )

    component = models.ForeignKey(

        SalaryComponent,

        on_delete=models.CASCADE,

        related_name="structure_links",

        db_column="salarycomponent_id",

    )

    use_component_default = models.BooleanField(

        default=True,

        help_text="If True, use master component rate. If False, use override fields below.",

    )

    override_calculation_type = models.CharField(

        max_length=20,

        choices=SalaryComponent.CalculationType.choices,

        blank=True,

        default="",

    )

    override_value = models.DecimalField(

        max_digits=12,

        decimal_places=2,

        null=True,

        blank=True,

    )



    class Meta:

        db_table = "payroll_salarystructure_applicable_components"

        unique_together = [("salary_structure", "component")]



    def __str__(self):

        return f"{self.salary_structure_id}:{self.component_id}"



    def clean(self):

        if not self.use_component_default:

            if self.override_calculation_type not in (

                SalaryComponent.CalculationType.PERCENTAGE,

                SalaryComponent.CalculationType.FIXED,

            ):

                raise ValidationError({"override_calculation_type": "Select percentage or fixed amount."})

            if self.override_value is None:

                raise ValidationError({"override_value": "Enter a value."})

            if self.override_value < 0:

                raise ValidationError({"override_value": "Value cannot be negative."})

            if self.override_calculation_type == SalaryComponent.CalculationType.PERCENTAGE:

                if self.override_value > 100:

                    raise ValidationError({"override_value": "Percentage cannot exceed 100."})





class SalaryAdvance(models.Model):

    class Status(models.TextChoices):

        ACTIVE = "ACTIVE", "Active"

        COMPLETED = "COMPLETED", "Completed"



    teacher = models.ForeignKey(

        "school_data.Teacher",

        on_delete=models.CASCADE,

        related_name="salary_advances",

    )

    amount = models.DecimalField(max_digits=12, decimal_places=2)

    advance_date = models.DateField()

    remaining_balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    monthly_deduction = models.DecimalField(max_digits=12, decimal_places=2)

    reason = models.CharField(max_length=200, blank=True)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)



    class Meta:

        ordering = ["-advance_date"]



    def __str__(self):

        return f"{self.teacher} - ₹{self.amount} ({self.status})"





class Payslip(models.Model):

    class Status(models.TextChoices):

        DRAFT = "DRAFT", "Draft"

        PROCESSED = "PROCESSED", "Processed"

        PAID = "PAID", "Paid"



    class PaymentMethod(models.TextChoices):

        BANK_TRANSFER = "Bank Transfer", "Bank Transfer"

        CASH = "Cash Payment", "Cash Payment"

        CHEQUE = "Cheque", "Cheque"

        UPI_DIGITAL = "UPI / Digital", "UPI / Digital"



    teacher = models.ForeignKey(

        "school_data.Teacher",

        on_delete=models.CASCADE,

        related_name="payslips",

    )

    month = models.PositiveSmallIntegerField()

    year = models.PositiveIntegerField()

    basic_salary = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    total_allowances = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    total_deductions = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    advance_deduction = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    net_salary = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    earnings_breakdown = models.JSONField(default=dict, blank=True)

    deductions_breakdown = models.JSONField(default=dict, blank=True)

    payment_date = models.DateField(null=True, blank=True)

    payment_method = models.CharField(
        max_length=50,
        blank=True,
        choices=PaymentMethod.choices,
        default=PaymentMethod.BANK_TRANSFER,
    )

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)



    class Meta:

        ordering = ["-year", "-month"]

        unique_together = [["teacher", "month", "year"]]



    def __str__(self):

        return f"{self.teacher} - {self.month}/{self.year}"



    @classmethod

    def normalize_payment_method(cls, raw):

        """Return a valid payment method value; default Bank Transfer."""

        allowed = {c.value for c in cls.PaymentMethod}

        t = (raw or "").strip()

        return t if t in allowed else cls.PaymentMethod.BANK_TRANSFER

