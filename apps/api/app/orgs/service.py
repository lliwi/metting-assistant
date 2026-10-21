from sqlalchemy.orm import Session

from app.models import Membership, Organization, Role, User


def create_organization(
    db: Session, owner: User, name: str, *, personal: bool = False
) -> Organization:
    org = Organization(name=name, is_personal=personal, settings={})
    db.add(org)
    db.flush()
    db.add(Membership(user_id=owner.id, organization_id=org.id, role=Role.OWNER))
    return org


def create_personal_organization(db: Session, user: User) -> Organization:
    return create_organization(db, user, user.name, personal=True)
