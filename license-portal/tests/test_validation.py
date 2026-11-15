"""Tests for license_portal.validation."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

# The repository root provides the shared ``pyshield`` package; the portal
# directory provides ``license_portal`` itself.
ROOT = Path(__file__).resolve().parents[2]  # .../PyShield
PORTAL = Path(__file__).resolve().parents[1]  # .../PyShield/license-portal
for _path in (ROOT, PORTAL):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from license_portal import validation
from license_portal.validation import ValidationError


def assert_invalid(testcase: unittest.TestCase, field: str, func, *args, **kwargs):
    with testcase.assertRaises(ValidationError) as captured:
        func(*args, **kwargs)
    testcase.assertIn(field, captured.exception.errors)
    return captured.exception


class EmailNormalizationTests(unittest.TestCase):
    def test_accepts_common_addresses_and_lowercases(self) -> None:
        self.assertEqual(
            validation.normalize_email("  User.Name+tag@Example.COM  "),
            "user.name+tag@example.com",
        )
        self.assertEqual(validation.normalize_email("a@b.co"), "a@b.co")

    def test_rejects_malformed_addresses(self) -> None:
        invalid = [
            "",
            "plainaddress",
            "a@b",
            "@example.com",
            "a@",
            "a@b.",
            ".a@example.com",
            "a.@example.com",
            "a..b@example.com",
            "a b@example.com",
            "a@-example.com",
            "a@example-.com",
            "a" * 65 + "@example.com",
            "a@" + "b" * 250 + ".com",
        ]
        for value in invalid:
            with self.subTest(value=value):
                assert_invalid(self, "email", validation.normalize_email, value)


class PhoneNormalizationTests(unittest.TestCase):
    def test_accepts_mainland_and_international_numbers(self) -> None:
        self.assertEqual(validation.normalize_phone("13800138000"), "13800138000")
        self.assertEqual(
            validation.normalize_phone("+8613800138000"), "+8613800138000",
        )
        self.assertEqual(
            validation.normalize_phone(" 138-0013-8000 "), "13800138000",
        )
        self.assertEqual(
            validation.normalize_phone("008613800138000"), "+8613800138000",
        )
        self.assertEqual(
            validation.normalize_phone("8613800138000"), "8613800138000",
        )

    def test_rejects_invalid_numbers(self) -> None:
        invalid = [
            "",
            "12345",
            "1234567890",
            "12345678901",  # 11 digits but not a valid mainland mobile prefix
            "1380013800",
            "1380013800a",
            "+1234567",
            "1234567890123456",  # too many digits
        ]
        for value in invalid:
            with self.subTest(value=value):
                assert_invalid(self, "phone", validation.normalize_phone, value)


class NameNormalizationTests(unittest.TestCase):
    def test_accepts_and_trims_names(self) -> None:
        self.assertEqual(validation.normalize_name("张三"), "张三")
        self.assertEqual(validation.normalize_name("  张三  "), "张三")
        self.assertEqual(validation.normalize_name("Li"), "Li")

    def test_enforces_length(self) -> None:
        assert_invalid(self, "name", validation.normalize_name, "")
        assert_invalid(self, "name", validation.normalize_name, "张")
        assert_invalid(self, "name", validation.normalize_name, "a" * 41)

    def test_rejects_control_and_invisible_characters(self) -> None:
        assert_invalid(self, "name", validation.normalize_name, "张\x00三")
        assert_invalid(self, "name", validation.normalize_name, "张\n三")
        assert_invalid(self, "name", validation.normalize_name, "张​三")


class OrganizationTests(unittest.TestCase):
    def test_org_type(self) -> None:
        self.assertEqual(validation.normalize_org_type("individual"), "individual")
        self.assertEqual(validation.normalize_org_type(" Company "), "company")
        assert_invalid(self, "org_type", validation.normalize_org_type, "")
        assert_invalid(self, "org_type", validation.normalize_org_type, "personal")

    def test_company_name_is_conditional(self) -> None:
        self.assertIsNone(validation.normalize_company_name("ACME Ltd", "individual"))
        self.assertIsNone(validation.normalize_company_name("", "individual"))
        self.assertEqual(
            validation.normalize_company_name("ACME Ltd", "company"), "ACME Ltd",
        )
        assert_invalid(self, "company_name", validation.normalize_company_name, "", "company")
        assert_invalid(self, "company_name", validation.normalize_company_name, "A", "company")
        assert_invalid(
            self, "company_name", validation.normalize_company_name, "A" * 101, "company",
        )


class SerialNormalizationTests(unittest.TestCase):
    def test_accepts_canonical_and_sloppy_serials(self) -> None:
        self.assertEqual(
            validation.normalize_serial("PS-ABCD-EFGH-JKMN"), "PS-ABCD-EFGH-JKMN",
        )
        self.assertEqual(
            validation.normalize_serial("psabcdefghjkmn"), "PS-ABCD-EFGH-JKMN",
        )
        self.assertEqual(
            validation.normalize_serial("ps abcd efgh jkmn"), "PS-ABCD-EFGH-JKMN",
        )

    def test_rejects_bad_serials(self) -> None:
        for value in ("", "PS-ABC", "PS-ABCD-EFGH-JKMI", "PS-ABCD-EFGH-JKMO", "PS-ABCD-EFGH-JKMU"):
            with self.subTest(value=value):
                assert_invalid(self, "serial", validation.normalize_serial, value)


class MachineNormalizationTests(unittest.TestCase):
    def test_accepts_32_hex_and_lowercases(self) -> None:
        uppercase = "0123456789ABCDEF0123456789ABCDEF"
        self.assertEqual(
            validation.normalize_machine(uppercase), uppercase.lower(),
        )
        self.assertEqual(
            validation.normalize_machine(" 0123456789abcdef0123456789abcdef "),
            "0123456789abcdef0123456789abcdef",
        )

    def test_rejects_bad_machines(self) -> None:
        for value in (
            "",
            "0123",
            "0123456789abcdef0123456789abcde",   # 31
            "0123456789abcdef0123456789abcdef0",  # 33
            "g" * 32,
            "0123456789abcdef0123456789abcde-",
        ):
            with self.subTest(value=value):
                assert_invalid(self, "machine", validation.normalize_machine, value)


class LicenseKeyNormalizationTests(unittest.TestCase):
    def test_generated_keys_have_expected_prefixes(self) -> None:
        self.assertTrue(validation.generate_license_key("trial").startswith("PST-"))
        self.assertTrue(validation.generate_license_key("pro").startswith("PSP-"))
        self.assertTrue(validation.generate_license_key("enterprise").startswith("PSE-"))
        with self.assertRaises(ValueError):
            validation.generate_license_key("unknown")

    def test_normalizes_case_and_separators(self) -> None:
        canonical = "PSP-9ABC-DEFG-HJKM-NPQR"
        self.assertEqual(validation.normalize_license_key(canonical), canonical)
        self.assertEqual(validation.normalize_license_key(canonical.lower()), canonical)
        self.assertEqual(
            validation.normalize_license_key("psp9abcdefghjkmnpqr"), canonical,
        )
        self.assertEqual(
            validation.normalize_license_key("  psp 9abc defg hjkm npqr  "), canonical,
        )

    def test_corrects_common_ocr_confusions(self) -> None:
        self.assertEqual(
            validation.normalize_license_key("PSP-OOOO-IIII-LLLL-0000"),
            "PSP-0000-1111-1111-0000",
        )

    def test_rejects_bad_keys(self) -> None:
        bad = [
            "",
            "---",
            "A",
            "XYZ-9ABC-DEFG-HJKM-NPQR",       # unknown prefix
            "PSP-ABCD-EFGH-JKMN",            # body too short
            "PSP-9ABC-DEFG-HJKM-NPQRX",      # body too long
            "PSP-UUUU-0000-0000-0000",       # U is not Crockford
        ]
        for value in bad:
            with self.subTest(value=value):
                assert_invalid(self, "key", validation.normalize_license_key, value)

    def test_looks_like_license_key(self) -> None:
        self.assertTrue(
            validation.looks_like_license_key("PSP-9ABC-DEFG-HJKM-NPQR"),
        )
        self.assertFalse(validation.looks_like_license_key("not-a-key"))


class TrialCodeNormalizationTests(unittest.TestCase):
    def test_generated_trial_codes_round_trip(self) -> None:
        code = validation.generate_trial_code()
        self.assertRegex(code, r"^PST(?:-[0-9A-HJKMNP-TV-Z]{4}){3}$")
        self.assertEqual(validation.normalize_trial_code(code), code)

    def test_accepts_lowercase_and_missing_hyphens(self) -> None:
        canonical = "PST-ABCD-EFGH-JKMN"
        self.assertEqual(validation.normalize_trial_code(canonical.lower()), canonical)
        self.assertEqual(validation.normalize_trial_code("pstabcdefghjkmn"), canonical)

    def test_rejects_bad_trial_codes(self) -> None:
        for value in (
            "",
            "PST-ABCD",
            "PSP-ABCD-EFGH-JKMN",
            "PST-ABCD-EFGH-JKMI",
            "PST-UUUU-0000-0000",
        ):
            with self.subTest(value=value):
                assert_invalid(self, "code", validation.normalize_trial_code, value)


class MaskingTests(unittest.TestCase):
    def test_mask_email(self) -> None:
        self.assertEqual(validation.mask_email("alice@example.com"), "a***e@example.com")
        self.assertEqual(validation.mask_email("bob@example.com"), "b*b@example.com")
        self.assertEqual(validation.mask_email("ab@example.com"), "a*@example.com")
        self.assertEqual(validation.mask_email("a@example.com"), "a*@example.com")
        self.assertEqual(validation.mask_email("no-at-sign"), "no-at-sign")
        self.assertEqual(validation.mask_email(""), "")

    def test_mask_phone(self) -> None:
        self.assertEqual(validation.mask_phone("13800138000"), "138****8000")
        self.assertEqual(validation.mask_phone("+8613800138000"), "861****8000")
        self.assertEqual(validation.mask_phone("+86 138 0013 8000"), "861****8000")
        self.assertEqual(validation.mask_phone("12345"), "12345")
        self.assertEqual(validation.mask_phone(""), "")


class TrialApplicationTests(unittest.TestCase):
    def _payload(self, **overrides):
        payload = {
            "serial": "PS-ABCD-EFGH-JKMN",
            "machine": "0123456789abcdef0123456789abcdef",
            "email": "trial@example.com",
            "phone": "13800138000",
            "name": "张三",
            "org_type": "individual",
            "consent": "on",
        }
        payload.update(overrides)
        return payload

    def test_valid_individual_application(self) -> None:
        result = validation.validate_trial_application(self._payload())
        self.assertTrue(result["consent"])
        self.assertIsNone(result["company_name"])
        self.assertEqual(result["serial"], "PS-ABCD-EFGH-JKMN")
        self.assertEqual(result["machine"], "0123456789abcdef0123456789abcdef")

    def test_company_name_is_required_for_companies(self) -> None:
        errors = assert_invalid(
            self, "company_name",
            validation.validate_trial_application,
            self._payload(org_type="company"),
        )
        self.assertIn("company_name", errors.errors)
        result = validation.validate_trial_application(
            self._payload(org_type="company", company_name="ACME Ltd"),
        )
        self.assertEqual(result["company_name"], "ACME Ltd")

    def test_consent_is_required(self) -> None:
        for consent in ("", "no", "0"):
            with self.subTest(consent=consent):
                assert_invalid(
                    self, "consent",
                    validation.validate_trial_application,
                    self._payload(consent=consent),
                )

    def test_collects_multiple_errors(self) -> None:
        errors = assert_invalid(
            self, "email",
            validation.validate_trial_application,
            self._payload(email="bad", phone="bad", name="", consent=""),
        )
        for field in ("email", "phone", "name", "consent"):
            self.assertIn(field, errors.errors)


if __name__ == "__main__":
    unittest.main()
