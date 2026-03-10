"""
Payroll models. Tenant-schema (school_data). Teacher from school_data.
"""
from decimal import Decimal

from django.db import models


class SalaryComponent(models.Model):
    class ComponentType(models.TextChoices):
        ALLOWANCE = "ALLOWANCE", "Allowance"
        DEDUCTION = "DEDUCTION", "Deduction"

    class CalculationType(models.TextChoices):
        PERCENTAGE = "PERCENTAGE", "Percentage of Basic"
        FIXED = "FIXED", "Fixed Amount"

    name = models.CharField(max_length=100)
    component_type = models.CharField(max_length=20, choices=ComponentType.choices)
    calculation_type = models.CharField(max_length=20, choices=CalculationType.choices)
    value = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text="% or fixed amount")
    is_active = models.BooleanField(default=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["component_type", "order", "name"]

    def __str__(self):
        return self.name

    def calculate(self, basic_salary):
        if self.calculation_type == self.CalculationType.PERCENTAGE:
            return basic_salary * (self.value / Decimal("100"))
        return self.value


class SalaryStructure(models.Model):
    teacher = models.OneToOneField(
        "school_data.Teacher",
        on_delete=models.CASCADE,
        related_name="salary_structure",
    )
    designation = models.CharField(max_length=100, blank=True)
    department = models.CharField(max_length=100, blank=True)
    basic_salary = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    class Meta:
        ordering = ["teacher__user__first_name", "teacher__user__last_name"]

    def __str__(self):
        return f"{self.teacher} - {self.designation or 'N/A'}"

    def total_allowances(self):
        total = Decimal("0")
        for c in SalaryComponent.objects.filter(
            component_type=SalaryComponent.ComponentType.ALLOWANCE, is_active=True
        ):
            total += c.calculate(self.basic_salary)
        return total

    def total_deductions(self, advance_deduction=Decimal("0")):
        total = Decimal("0")
        for c in SalaryComponent.objects.filter(
            component_type=SalaryComponent.ComponentType.DEDUCTION, is_active=True
        ):
            total += c.calculate(self.basic_salary)
        return total + advance_deduction

    def net_salary(self, advance_deduction=Decimal("0")):
        return self.basic_salary + self.total_allowances() - self.total_deductions(advance_deduction)


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
    payment_method = models.CharField(max_length=50, blank=True, default="Bank Transfer")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)

    class Meta:
        ordering = ["-year", "-month"]
        unique_together = [["teacher", "month", "year"]]

    def __str__(self):
        return f"{self.teacher} - {self.month}/{self.year}"
