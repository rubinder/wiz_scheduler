from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.dependencies import get_current_user, get_db, require_manager
from backend.models import (
    Employee,
    EmployeeAffinity,
    EmployeeAvailability,
    EmployeeCompany,
    EmployeeInvite,
    EmployeeRole,
    Shift,
    User,
    UserConsent,
)
from backend.models.employee_role_minutes import EmployeeRoleMinutes

router = APIRouter(prefix="/gdpr", tags=["gdpr"])


# ---------------------------------------------------------------------------
# POST /gdpr/retention/purge  --  Manual data retention purge (manager only)
# ---------------------------------------------------------------------------
@router.post("/retention/purge")
async def trigger_retention_purge(
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Manually trigger data retention purge. Manager only."""
    from backend.services.data_retention import run_data_retention

    summary = await run_data_retention(db)
    return {"status": "completed", "summary": summary}


# ---------------------------------------------------------------------------
# GET /gdpr/export  --  Data export (right to access + portability)
# ---------------------------------------------------------------------------
@router.get("/export")
async def export_data(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    user_id = current_user.id

    # User profile
    user_data = {
        "id": current_user.id,
        "email": current_user.email,
        "full_name": current_user.full_name,
        "user_role": current_user.user_role,
        "company_id": current_user.company_id,
    }

    # Linked employee profile
    emp_result = await db.execute(
        select(Employee).where(Employee.user_id == user_id)
    )
    employee = emp_result.scalar_one_or_none()

    employee_data = None
    availability_data: list[dict] = []
    affinity_data: list[dict] = []
    shift_data: list[dict] = []

    if employee:
        # Roles
        role_result = await db.execute(
            select(EmployeeRole).where(EmployeeRole.employee_id == employee.id)
        )
        roles = [
            {"id": r.id, "role_id": r.role_id, "skill_level": r.skill_level}
            for r in role_result.scalars().all()
        ]

        employee_data = {
            "id": employee.id,
            "full_name": employee.full_name,
            "email": employee.email,
            "location_ids": employee.location_ids,
            "roles": roles,
        }

        # Availability
        avail_result = await db.execute(
            select(EmployeeAvailability).where(
                EmployeeAvailability.employee_id == employee.id
            )
        )
        availability_data = [
            {
                "id": a.id,
                "year": a.year,
                "month": a.month,
                "day": a.day,
                "start_time": a.start_time.isoformat() if a.start_time else None,
                "end_time": a.end_time.isoformat() if a.end_time else None,
            }
            for a in avail_result.scalars().all()
        ]

        # Affinities (as source or target)
        aff_result = await db.execute(
            select(EmployeeAffinity).where(
                (EmployeeAffinity.employee_id == employee.id)
                | (EmployeeAffinity.target_employee_id == employee.id)
            )
        )
        affinity_data = [
            {
                "id": a.id,
                "employee_id": a.employee_id,
                "target_employee_id": a.target_employee_id,
                "level": float(a.level),
                "entry_date": a.entry_date.isoformat(),
                "expiration_date": a.expiration_date.isoformat() if a.expiration_date else None,
            }
            for a in aff_result.scalars().all()
        ]

        # Shifts
        shift_result = await db.execute(
            select(Shift).where(Shift.employee_id == employee.id)
        )
        shift_data = [
            {
                "id": s.id,
                "shift_schedule_id": s.shift_schedule_id,
                "location_id": s.location_id,
                "role_id": s.role_id,
                "role_name": s.role_name,
                "date": s.date.isoformat(),
                "start_time": s.start_time.isoformat(),
                "end_time": s.end_time.isoformat(),
            }
            for s in shift_result.scalars().all()
        ]

    # Consents
    consent_result = await db.execute(
        select(UserConsent).where(UserConsent.user_id == user_id)
    )
    consent_data = [
        {
            "id": c.id,
            "consent_type": c.consent_type,
            "version": c.version,
            "granted_at": c.granted_at.isoformat() if c.granted_at else None,
            "revoked_at": c.revoked_at.isoformat() if c.revoked_at else None,
            "ip_address": c.ip_address,
        }
        for c in consent_result.scalars().all()
    ]

    return {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "user": user_data,
        "employee": employee_data,
        "availability": availability_data,
        "affinities": affinity_data,
        "shifts": shift_data,
        "consents": consent_data,
    }


# ---------------------------------------------------------------------------
# DELETE /gdpr/delete-account  --  Right to erasure
# ---------------------------------------------------------------------------
@router.delete("/delete-account")
async def delete_account(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    user_id = current_user.id

    # 1. Delete consent records
    await db.execute(
        delete(UserConsent).where(UserConsent.user_id == user_id)
    )

    # 2. If user has a linked employee, delete associated data
    emp_result = await db.execute(
        select(Employee).where(Employee.user_id == user_id)
    )
    employee = emp_result.scalar_one_or_none()

    if employee:
        emp_id = employee.id

        # Delete shifts
        await db.execute(
            delete(Shift).where(Shift.employee_id == emp_id)
        )
        # Delete availability
        await db.execute(
            delete(EmployeeAvailability).where(
                EmployeeAvailability.employee_id == emp_id
            )
        )
        # Delete employee roles
        await db.execute(
            delete(EmployeeRole).where(EmployeeRole.employee_id == emp_id)
        )
        # Delete affinities (both directions)
        await db.execute(
            delete(EmployeeAffinity).where(
                (EmployeeAffinity.employee_id == emp_id)
                | (EmployeeAffinity.target_employee_id == emp_id)
            )
        )
        # Delete employee-company links
        await db.execute(
            delete(EmployeeCompany).where(EmployeeCompany.employee_id == emp_id)
        )
        # Delete employee invites
        await db.execute(
            delete(EmployeeInvite).where(EmployeeInvite.employee_id == emp_id)
        )
        # Delete employee role minutes
        await db.execute(
            delete(EmployeeRoleMinutes).where(
                EmployeeRoleMinutes.employee_id == emp_id
            )
        )
        # Delete the employee record
        await db.execute(
            delete(Employee).where(Employee.id == emp_id)
        )

    # 3. Delete the user record
    await db.execute(
        delete(User).where(User.id == user_id)
    )

    await db.commit()
    return {"deleted": True}


# ---------------------------------------------------------------------------
# GET /gdpr/consents  --  List user consent records
# ---------------------------------------------------------------------------
@router.get("/consents")
async def list_consents(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    result = await db.execute(
        select(UserConsent).where(UserConsent.user_id == current_user.id)
    )
    return [
        {
            "id": c.id,
            "consent_type": c.consent_type,
            "version": c.version,
            "granted_at": c.granted_at.isoformat() if c.granted_at else None,
            "revoked_at": c.revoked_at.isoformat() if c.revoked_at else None,
            "ip_address": c.ip_address,
        }
        for c in result.scalars().all()
    ]


# ---------------------------------------------------------------------------
# POST /gdpr/consents/revoke  --  Revoke a specific consent
# ---------------------------------------------------------------------------
@router.post("/consents/revoke")
async def revoke_consent(
    consent_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Revoke a previously granted consent."""
    result = await db.execute(
        select(UserConsent).where(
            UserConsent.id == consent_id,
            UserConsent.user_id == current_user.id,
            UserConsent.revoked_at.is_(None),
        )
    )
    consent = result.scalar_one_or_none()
    if not consent:
        raise HTTPException(status_code=404, detail="Consent not found or already revoked")
    consent.revoked_at = datetime.now(timezone.utc)
    await db.commit()
    return {"revoked": True, "consent_id": consent_id}


# ---------------------------------------------------------------------------
# GET /gdpr/privacy-policy  --  Public privacy policy (no auth)
# ---------------------------------------------------------------------------
@router.get("/privacy-policy")
async def privacy_policy() -> dict:
    return {
        "version": "1.0",
        "effective_date": "2026-04-05",
        "content": (
            "PRIVACY POLICY FOR WIZSCHEDULER\n\n"
            "Effective Date: April 5, 2026\n\n"
            "1. INTRODUCTION\n"
            "WizScheduler (\"we\", \"our\", \"us\") is a multi-tenant employee scheduling "
            "platform. This Privacy Policy explains how we collect, use, disclose, and "
            "safeguard your personal data when you use our service. We are committed to "
            "protecting your privacy in compliance with the General Data Protection "
            "Regulation (GDPR), the California Consumer Privacy Act (CCPA), and other "
            "applicable data protection laws.\n\n"
            "2. DATA WE COLLECT\n"
            "We collect the following categories of personal data:\n"
            "- Account Information: full name, email address, hashed password.\n"
            "- Employee Profile Data: full name, email, assigned roles, skill levels, "
            "work location assignments.\n"
            "- Availability Data: weekly availability windows (day, start time, end time).\n"
            "- Work Preference Data: interpersonal affinity scores between employees "
            "(used to optimize team composition).\n"
            "- Shift & Schedule Data: assigned shifts, schedule history, role-based "
            "working minutes.\n"
            "- Technical Data: IP addresses (recorded with consent actions), JWT "
            "authentication tokens (not stored server-side beyond session).\n"
            "- Usage Data: API request logs for error monitoring and service improvement.\n\n"
            "3. HOW WE USE YOUR DATA\n"
            "We process your personal data for the following purposes:\n"
            "- Schedule Generation: Your availability, roles, skill levels, and affinity "
            "data are sent to Anthropic's Claude AI to generate optimized weekly shift "
            "schedules. Data is transmitted via encrypted API calls and is not retained "
            "by Anthropic beyond the API request lifecycle.\n"
            "- Account Management: To create and maintain your user account, authenticate "
            "sessions via JWT, and manage multi-tenant company access.\n"
            "- Communication: To send transactional emails (e.g., welcome emails, "
            "employee invitations) via our email service provider, Resend.\n"
            "- Data Import/Export: To facilitate integration with 7shifts for importing "
            "and exporting employee and schedule data at your manager's request.\n"
            "- Service Improvement: To monitor errors, analyze usage patterns, and "
            "improve scheduling algorithms.\n\n"
            "4. LEGAL BASIS FOR PROCESSING (GDPR)\n"
            "We process your data based on:\n"
            "- Consent: You provide explicit consent during registration for data "
            "processing and AI-powered scheduling.\n"
            "- Contractual Necessity: Processing is necessary to provide the scheduling "
            "service you have requested.\n"
            "- Legitimate Interest: For security, fraud prevention, and service "
            "improvement.\n\n"
            "5. DATA SHARING AND THIRD-PARTY PROCESSORS\n"
            "We share your data with the following third-party processors:\n"
            "- Anthropic (Claude AI): Employee availability, roles, and preferences are "
            "sent for AI-powered schedule generation. Anthropic processes data per their "
            "data processing terms and does not use API inputs for model training.\n"
            "- 7shifts: Employee and schedule data may be imported from or exported to "
            "7shifts at your organization's discretion.\n"
            "- Resend: Email addresses and names are shared to deliver transactional "
            "emails.\n"
            "- Cloud Infrastructure Provider: All data is hosted on secured cloud "
            "infrastructure with encryption at rest and in transit.\n\n"
            "6. DATA RETENTION\n"
            "We retain your personal data for as long as your account is active or as "
            "needed to provide services. Upon account deletion (right to erasure), all "
            "personal data is permanently deleted, including employee profiles, "
            "availability, affinities, shift history, and consent records.\n\n"
            "7. YOUR RIGHTS\n"
            "Under GDPR and applicable laws, you have the right to:\n"
            "- Access: Request a copy of all personal data we hold about you (GET "
            "/api/v1/gdpr/export).\n"
            "- Rectification: Update or correct your personal data.\n"
            "- Erasure: Request deletion of your account and all associated data "
            "(DELETE /api/v1/gdpr/delete-account).\n"
            "- Data Portability: Receive your data in a structured, machine-readable "
            "JSON format.\n"
            "- Withdraw Consent: You may withdraw consent at any time, though this may "
            "limit your ability to use the service.\n"
            "- Restriction of Processing: Request that we limit how we use your data.\n"
            "- Object: Object to processing based on legitimate interests.\n\n"
            "8. DATA SECURITY\n"
            "We implement appropriate technical and organizational measures to protect "
            "your data, including:\n"
            "- Passwords are hashed using bcrypt and never stored in plaintext.\n"
            "- Authentication via signed JWT tokens with configurable expiration.\n"
            "- Multi-tenant data isolation ensures company data is strictly separated.\n"
            "- All API communications are encrypted via TLS.\n"
            "- Security headers (X-Content-Type-Options, X-Frame-Options, HSTS) are "
            "enforced.\n\n"
            "9. INTERNATIONAL DATA TRANSFERS\n"
            "Your data may be transferred to and processed in countries outside your "
            "jurisdiction. We ensure appropriate safeguards are in place, including "
            "Standard Contractual Clauses where required.\n\n"
            "10. CHILDREN'S PRIVACY\n"
            "Our service is not directed to individuals under 16. We do not knowingly "
            "collect personal data from children.\n\n"
            "11. CHANGES TO THIS POLICY\n"
            "We may update this Privacy Policy from time to time. We will notify you of "
            "material changes via email or in-app notification. Continued use of the "
            "service after changes constitutes acceptance.\n\n"
            "12. CONTACT\n"
            "For privacy inquiries, data subject requests, or complaints, contact us at "
            "privacy@wizscheduler.com."
        ),
    }


# ---------------------------------------------------------------------------
# GET /gdpr/terms  --  Public terms of service (no auth)
# ---------------------------------------------------------------------------
@router.get("/terms")
async def terms_of_service() -> dict:
    return {
        "version": "1.0",
        "effective_date": "2026-04-05",
        "content": (
            "TERMS OF SERVICE FOR WIZSCHEDULER\n\n"
            "Effective Date: April 5, 2026\n\n"
            "1. ACCEPTANCE OF TERMS\n"
            "By accessing or using WizScheduler (\"the Service\"), you agree to be bound "
            "by these Terms of Service (\"Terms\"). If you do not agree, you may not use "
            "the Service.\n\n"
            "2. DESCRIPTION OF SERVICE\n"
            "WizScheduler is a SaaS employee scheduling platform that uses AI to "
            "generate optimized weekly shift schedules. The Service includes employee "
            "management, availability tracking, role assignment, shift generation, and "
            "schedule export features.\n\n"
            "3. ACCOUNTS AND REGISTRATION\n"
            "You must register for an account to use the Service. You agree to provide "
            "accurate information, maintain the security of your credentials, and accept "
            "responsibility for all activity under your account. You must accept our "
            "Privacy Policy and these Terms during registration.\n\n"
            "4. MULTI-TENANT ARCHITECTURE\n"
            "The Service operates as a multi-tenant platform. Each company's data is "
            "logically isolated. You may only access data belonging to your authorized "
            "company. Unauthorized access to another tenant's data is strictly "
            "prohibited.\n\n"
            "5. ACCEPTABLE USE\n"
            "You agree not to:\n"
            "- Use the Service for any unlawful purpose.\n"
            "- Attempt to gain unauthorized access to other accounts or systems.\n"
            "- Reverse-engineer, decompile, or disassemble any part of the Service.\n"
            "- Transmit malicious code or interfere with the Service's operation.\n"
            "- Use the Service to discriminate against employees based on protected "
            "characteristics.\n"
            "- Exceed reasonable usage limits or abuse API endpoints.\n\n"
            "6. AI-GENERATED SCHEDULES\n"
            "Schedules generated by WizScheduler's AI are suggestions and should be "
            "reviewed by a manager before publication. We do not guarantee that generated "
            "schedules will be free of conflicts or comply with all applicable labor "
            "laws. You are responsible for ensuring compliance with local employment "
            "regulations, including maximum working hours, rest periods, and minimum "
            "wage requirements.\n\n"
            "7. DATA OWNERSHIP\n"
            "You retain ownership of all data you input into the Service. By using the "
            "Service, you grant us a limited license to process your data solely for the "
            "purpose of providing the Service, including transmitting data to third-party "
            "processors (see Privacy Policy for details).\n\n"
            "8. INTELLECTUAL PROPERTY\n"
            "The Service, including its software, algorithms, design, and documentation, "
            "is owned by WizScheduler and protected by intellectual property laws. These "
            "Terms do not grant you any rights to our intellectual property except the "
            "limited right to use the Service.\n\n"
            "9. THIRD-PARTY INTEGRATIONS\n"
            "The Service may integrate with third-party services such as 7shifts. We are "
            "not responsible for the availability, accuracy, or practices of third-party "
            "services. Your use of third-party integrations is subject to those "
            "providers' terms.\n\n"
            "10. SERVICE AVAILABILITY\n"
            "We strive to maintain high availability but do not guarantee uninterrupted "
            "access. We may perform maintenance, updates, or modifications that "
            "temporarily affect availability. We will provide reasonable notice of "
            "planned downtime.\n\n"
            "11. LIMITATION OF LIABILITY\n"
            "TO THE MAXIMUM EXTENT PERMITTED BY LAW, WIZSCHEDULER SHALL NOT BE LIABLE "
            "FOR ANY INDIRECT, INCIDENTAL, SPECIAL, CONSEQUENTIAL, OR PUNITIVE DAMAGES, "
            "INCLUDING BUT NOT LIMITED TO LOSS OF PROFITS, DATA, OR BUSINESS "
            "OPPORTUNITIES, ARISING FROM YOUR USE OF THE SERVICE.\n\n"
            "12. INDEMNIFICATION\n"
            "You agree to indemnify and hold harmless WizScheduler from any claims, "
            "damages, or expenses arising from your use of the Service, your violation "
            "of these Terms, or your violation of any rights of a third party.\n\n"
            "13. TERMINATION\n"
            "Either party may terminate this agreement at any time. You may delete your "
            "account via the GDPR deletion endpoint. We may suspend or terminate your "
            "access if you violate these Terms. Upon termination, your data will be "
            "deleted in accordance with our Privacy Policy.\n\n"
            "14. GOVERNING LAW\n"
            "These Terms shall be governed by and construed in accordance with the laws "
            "of the jurisdiction in which WizScheduler operates, without regard to "
            "conflict of law principles.\n\n"
            "15. CHANGES TO TERMS\n"
            "We reserve the right to modify these Terms at any time. Material changes "
            "will be communicated via email or in-app notification. Continued use of the "
            "Service after changes constitutes acceptance.\n\n"
            "16. CONTACT\n"
            "For questions about these Terms, contact us at legal@wizscheduler.com."
        ),
    }


# ---------------------------------------------------------------------------
# GET /gdpr/dpa  --  Data Processing Agreement info (no auth)
# ---------------------------------------------------------------------------
@router.get("/dpa")
async def data_processing_agreement() -> dict:
    return {
        "version": "1.0",
        "effective_date": "2026-04-05",
        "processors": [
            {
                "name": "Anthropic",
                "purpose": "AI-powered schedule generation via Claude API",
                "data_processed": (
                    "Employee names, roles, skill levels, availability windows, "
                    "and interpersonal affinity scores"
                ),
                "location": "United States",
                "safeguards": (
                    "Data transmitted via encrypted API calls; not retained beyond "
                    "request lifecycle; not used for model training"
                ),
            },
            {
                "name": "7shifts",
                "purpose": "Employee and schedule data import/export",
                "data_processed": (
                    "Employee names, roles, shift schedules, and related workforce data"
                ),
                "location": "Canada",
                "safeguards": (
                    "Data transferred only at customer's explicit request; encrypted "
                    "in transit via TLS"
                ),
            },
            {
                "name": "Resend",
                "purpose": "Transactional email delivery",
                "data_processed": "Email addresses, recipient names, email content",
                "location": "United States",
                "safeguards": (
                    "Data processed solely for email delivery; encrypted in transit; "
                    "subject to Resend's data processing terms"
                ),
            },
            {
                "name": "PostgreSQL Hosting Provider",
                "purpose": "Primary database hosting and storage",
                "data_processed": (
                    "All application data including user accounts, employee profiles, "
                    "availability, schedules, and consent records"
                ),
                "location": "Configured per deployment",
                "safeguards": (
                    "Encryption at rest and in transit; access restricted to "
                    "application service; regular backups with encryption"
                ),
            },
        ],
        "content": (
            "DATA PROCESSING AGREEMENT FOR WIZSCHEDULER\n\n"
            "Effective Date: April 5, 2026\n\n"
            "1. SCOPE AND PURPOSE\n"
            "This Data Processing Agreement (\"DPA\") forms part of the Terms of Service "
            "between WizScheduler (\"Processor\") and the customer organization "
            "(\"Controller\"). It governs the processing of personal data by the Processor "
            "on behalf of the Controller in connection with the WizScheduler scheduling "
            "service.\n\n"
            "2. DEFINITIONS\n"
            "Terms used in this DPA have the meanings given in the GDPR (Regulation (EU) "
            "2016/679) unless otherwise defined herein. \"Personal Data\" means any data "
            "relating to an identified or identifiable natural person processed through "
            "the Service.\n\n"
            "3. PROCESSING DETAILS\n"
            "- Subject Matter: Provision of AI-powered employee scheduling services.\n"
            "- Duration: For the term of the service agreement.\n"
            "- Nature and Purpose: Storage, retrieval, and AI-based optimization of "
            "employee scheduling data.\n"
            "- Types of Personal Data: Names, email addresses, work availability, role "
            "assignments, skill levels, interpersonal preferences, shift history.\n"
            "- Categories of Data Subjects: Employees and managers of the Controller's "
            "organization.\n\n"
            "4. OBLIGATIONS OF THE PROCESSOR\n"
            "The Processor shall:\n"
            "- Process Personal Data only on documented instructions from the Controller.\n"
            "- Ensure that persons authorized to process Personal Data have committed to "
            "confidentiality.\n"
            "- Implement appropriate technical and organizational security measures.\n"
            "- Assist the Controller in responding to data subject rights requests.\n"
            "- Delete or return all Personal Data upon termination of the service.\n"
            "- Make available all information necessary to demonstrate compliance.\n"
            "- Notify the Controller without undue delay of any Personal Data breach.\n\n"
            "5. SUB-PROCESSORS\n"
            "The Controller authorizes the use of the sub-processors listed in the "
            "\"processors\" field of this document. The Processor shall notify the "
            "Controller of any intended changes to sub-processors, giving the Controller "
            "the opportunity to object. The Processor shall ensure that sub-processors "
            "are bound by equivalent data protection obligations.\n\n"
            "6. DATA TRANSFERS\n"
            "Where Personal Data is transferred outside the EEA, the Processor ensures "
            "appropriate safeguards are in place, including:\n"
            "- EU Standard Contractual Clauses (SCCs) with each sub-processor.\n"
            "- Transfer Impact Assessments where required.\n"
            "- Supplementary measures as necessary to ensure adequate protection.\n\n"
            "7. SECURITY MEASURES\n"
            "The Processor implements the following measures:\n"
            "- Encryption of data at rest and in transit (TLS 1.2+).\n"
            "- Password hashing using bcrypt.\n"
            "- JWT-based authentication with configurable token expiration.\n"
            "- Multi-tenant data isolation at the application and database level.\n"
            "- Security headers (HSTS, X-Frame-Options, X-Content-Type-Options).\n"
            "- Regular security updates and vulnerability monitoring.\n\n"
            "8. DATA BREACH NOTIFICATION\n"
            "In the event of a Personal Data breach, the Processor shall notify the "
            "Controller within 72 hours of becoming aware of the breach, providing:\n"
            "- The nature of the breach and categories of data affected.\n"
            "- Approximate number of data subjects affected.\n"
            "- Likely consequences of the breach.\n"
            "- Measures taken or proposed to mitigate the breach.\n\n"
            "9. AUDITS AND INSPECTIONS\n"
            "The Processor shall allow for and contribute to audits and inspections "
            "conducted by the Controller or an authorized auditor. The Processor shall "
            "provide the Controller with all information necessary to demonstrate "
            "compliance with this DPA.\n\n"
            "10. TERMINATION\n"
            "Upon termination of the service agreement, the Processor shall, at the "
            "Controller's choice, delete or return all Personal Data and certify "
            "deletion, unless retention is required by applicable law.\n\n"
            "11. GOVERNING LAW\n"
            "This DPA shall be governed by the same law that governs the Terms of "
            "Service, supplemented by GDPR requirements where applicable."
        ),
    }
