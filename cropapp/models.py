import json
from datetime import date, datetime, timezone

from flask_login import UserMixin, current_user
from werkzeug.security import check_password_hash, generate_password_hash

from cropapp.extensions import db, login_manager

FEATURE_KEYS = ["N", "P", "K", "temperature", "humidity", "ph", "rainfall"]


def utcnow():
    """Naive UTC timestamp, matching the DateTime columns in this schema."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    language = db.Column(db.String(8), default="en", nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)

    farms = db.relationship("Farm", back_populates="owner", cascade="all, delete-orphan", lazy="selectin")
    recommendations = db.relationship("Recommendation", back_populates="owner", cascade="all, delete-orphan")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def greeting(self):
        return self.name.split("@")[0].split(" ")[0].title()


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


class Farm(db.Model):
    __tablename__ = "farms"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False, default="My Farm")
    area_acres = db.Column(db.Float)
    crop = db.Column(db.String(60))
    crop_category = db.Column(db.String(40))

    water_source = db.Column(db.String(40))
    water_days = db.Column(db.Float)
    water_note = db.Column(db.String(255))

    share_token = db.Column(db.String(32), unique=True, index=True)

    lat = db.Column(db.Float)
    lon = db.Column(db.Float)
    district = db.Column(db.String(120))
    state = db.Column(db.String(120))
    pincode = db.Column(db.String(16))
    village = db.Column(db.String(160))

    soil_ph = db.Column(db.Float)
    soil_n = db.Column(db.Float)
    soil_p = db.Column(db.Float)
    soil_k = db.Column(db.Float)
    ph_source = db.Column(db.String(200))
    texture_class = db.Column(db.String(60))
    sand_pct = db.Column(db.Float)
    clay_pct = db.Column(db.Float)
    silt_pct = db.Column(db.Float)
    organic_carbon = db.Column(db.Float)

    temp_c = db.Column(db.Float)
    humidity_pct = db.Column(db.Float)
    rainfall_mm = db.Column(db.Float)
    resolve_json = db.Column(db.Text)

    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    owner = db.relationship("User", back_populates="farms")
    recommendations = db.relationship(
        "Recommendation", back_populates="farm", cascade="all, delete-orphan", lazy="selectin"
    )
    tasks = db.relationship("FarmTask", back_populates="farm", cascade="all, delete-orphan", lazy="selectin")
    notes = db.relationship(
        "FarmNote", back_populates="farm", cascade="all, delete-orphan", lazy="selectin",
        order_by="FarmNote.created_at.desc()",
    )
    records = db.relationship(
        "FarmRecord", back_populates="farm", cascade="all, delete-orphan", lazy="selectin",
        order_by="FarmRecord.recorded_on.desc()",
    )
    budgets = db.relationship(
        "FarmBudget", back_populates="farm", cascade="all, delete-orphan", lazy="selectin",
    )

    @property
    def has_npk(self):
        return all(v is not None for v in (self.soil_n, self.soil_p, self.soil_k))

    @property
    def resolve(self):
        if not self.resolve_json:
            return None
        try:
            return json.loads(self.resolve_json)
        except ValueError:
            return None

    @property
    def has_ph(self):
        return self.soil_ph is not None

    @classmethod
    def from_resolution(cls, owner, name, area_acres, resolved, npk=None, ph=None):
        soil = resolved.get("soil") or {}
        climate = resolved.get("climate") or {}
        location = resolved.get("location") or {}
        available = soil.get("available") or {}
        farm = cls(
            owner=owner,
            name=name or "My Farm",
            area_acres=area_acres,
            lat=resolved.get("lat"),
            lon=resolved.get("lon"),
            district=location.get("district"),
            state=location.get("state"),
            pincode=location.get("pincode"),
            village=location.get("village"),
            soil_ph=ph if ph is not None else soil.get("ph"),
            soil_n=npk[0] if npk else available.get("n"),
            soil_p=npk[1] if npk else available.get("p"),
            soil_k=npk[2] if npk else available.get("k"),
            ph_source=soil.get("ph_source"),
            texture_class=soil.get("texture_class"),
            sand_pct=soil.get("sand_pct"),
            clay_pct=soil.get("clay_pct"),
            silt_pct=soil.get("silt_pct"),
            organic_carbon=soil.get("organic_carbon_g_kg"),
            temp_c=climate.get("temp_c"),
            humidity_pct=climate.get("humidity_pct"),
            rainfall_mm=climate.get("rainfall_model_mm"),
            resolve_json=json.dumps(resolved, ensure_ascii=False),
        )
        return farm


class Recommendation(db.Model):
    __tablename__ = "recommendations"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    farm_id = db.Column(db.Integer, db.ForeignKey("farms.id", ondelete="CASCADE"), index=True)

    features_json = db.Column(db.Text, nullable=False)
    top3_json = db.Column(db.Text)
    crop = db.Column(db.String(60), nullable=False)
    crop_category = db.Column(db.String(40))
    confidence = db.Column(db.Float)
    fertilizer_json = db.Column(db.Text)
    msp_json = db.Column(db.Text)
    water_json = db.Column(db.Text)
    warnings_json = db.Column(db.Text)
    spray_json = db.Column(db.Text)
    excluded_json = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=utcnow)

    owner = db.relationship("User", back_populates="recommendations")
    farm = db.relationship("Farm", back_populates="recommendations")
    tasks = db.relationship("FarmTask", back_populates="recommendation", lazy="selectin")

    @property
    def features(self):
        try:
            return json.loads(self.features_json)
        except ValueError:
            return {}

    @property
    def top3(self):
        try:
            return json.loads(self.top3_json or "[]")
        except ValueError:
            return []

    @property
    def fertilizer(self):
        try:
            return json.loads(self.fertilizer_json or "{}")
        except ValueError:
            return {}

    @property
    def msp(self):
        try:
            return json.loads(self.msp_json or "{}")
        except ValueError:
            return {}

    @property
    def warnings(self):
        try:
            return json.loads(self.warnings_json or "[]")
        except ValueError:
            return []

    @property
    def water(self):
        try:
            return json.loads(self.water_json or "null")
        except ValueError:
            return None

    @property
    def spray(self):
        try:
            return json.loads(self.spray_json or "null")
        except ValueError:
            return None

    @property
    def excluded(self):
        try:
            return json.loads(self.excluded_json or "[]")
        except ValueError:
            return []


class FarmNote(db.Model):
    """Advice saved from the Gemini chat for one farm (shown on the farm's calendar page)."""

    __tablename__ = "farm_notes"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    farm_id = db.Column(db.Integer, db.ForeignKey("farms.id", ondelete="CASCADE"), nullable=False, index=True)
    question = db.Column(db.Text)
    text = db.Column(db.Text, nullable=False)
    source = db.Column(db.String(30), default="ai")
    created_at = db.Column(db.DateTime, default=utcnow)

    farm = db.relationship("Farm", back_populates="notes")

    def as_dict(self):
        return {
            "id": self.id,
            "question": self.question,
            "text": self.text,
            "source": self.source,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class FarmRecord(db.Model):
    """A dated entry in a farm's history: soil test, expense, harvest, irrigation or note.

    Kept as a separate table so existing databases are extended safely (the table is
    created on startup; no column migration of existing rows is needed).
    """

    __tablename__ = "farm_records"

    KINDS = ("soil", "expense", "harvest", "irrigation", "observation")

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    farm_id = db.Column(db.Integer, db.ForeignKey("farms.id", ondelete="CASCADE"), nullable=False, index=True)
    kind = db.Column(db.String(30), nullable=False, default="observation")
    recorded_on = db.Column(db.Date, default=date.today, index=True)
    crop = db.Column(db.String(60))
    amount = db.Column(db.Float)
    quantity = db.Column(db.Float)
    unit = db.Column(db.String(20))
    # Expense-specific columns (added later; safe ALTER TABLE ADD COLUMN on sqlite).
    category = db.Column(db.String(40))
    season = db.Column(db.String(40))
    vendor = db.Column(db.String(120))
    payment_method = db.Column(db.String(30))
    data_json = db.Column(db.Text)
    note = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=utcnow)

    farm = db.relationship("Farm", back_populates="records")

    @property
    def data(self):
        try:
            return json.loads(self.data_json or "{}")
        except ValueError:
            return {}

    @property
    def expense_category(self):
        """Category key for an expense row, reading the new column then the legacy JSON."""
        return (self.category or self.data.get("category") or "other")

    @property
    def expense_season(self):
        return self.season or self.data.get("season") or None

    @property
    def expense_vendor(self):
        return self.vendor or self.data.get("vendor") or None

    def as_dict(self):
        return {
            "id": self.id,
            "kind": self.kind,
            "recorded_on": self.recorded_on.isoformat() if self.recorded_on else None,
            "crop": self.crop,
            "amount": self.amount,
            "quantity": self.quantity,
            "unit": self.unit,
            "category": self.category,
            "season": self.season,
            "vendor": self.vendor,
            "payment_method": self.payment_method,
            "data": self.data,
            "note": self.note,
        }


class FarmBudget(db.Model):
    """A spending budget for a whole farm, one crop, or one growing season."""

    __tablename__ = "farm_budgets"

    SCOPES = ("farm", "crop", "season")

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    farm_id = db.Column(db.Integer, db.ForeignKey("farms.id", ondelete="CASCADE"), nullable=False, index=True)
    scope = db.Column(db.String(20), nullable=False, default="farm")
    crop = db.Column(db.String(60))
    season = db.Column(db.String(40))
    amount = db.Column(db.Float, nullable=False)
    warn_pct = db.Column(db.Integer, default=80, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    farm = db.relationship("Farm", back_populates="budgets")

    def matches(self, crop=None, season=None):
        if self.scope == "crop":
            return bool(self.crop) and self.crop == crop
        if self.scope == "season":
            return bool(self.season) and self.season == season
        return True

    def as_dict(self):
        return {
            "id": self.id,
            "scope": self.scope,
            "crop": self.crop,
            "season": self.season,
            "amount": self.amount,
            "warn_pct": self.warn_pct,
        }


class FarmTask(db.Model):
    __tablename__ = "farm_tasks"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    farm_id = db.Column(db.Integer, db.ForeignKey("farms.id", ondelete="CASCADE"), nullable=False, index=True)
    recommendation_id = db.Column(db.Integer, db.ForeignKey("recommendations.id", ondelete="SET NULL"), index=True)

    kind = db.Column(db.String(30), nullable=False, default="other")
    title_en = db.Column(db.String(255), nullable=False)
    title_mr = db.Column(db.String(255))
    detail_en = db.Column(db.Text)
    detail_mr = db.Column(db.Text)
    due_date = db.Column(db.Date, index=True)
    status = db.Column(db.String(20), default="pending", nullable=False)
    source = db.Column(db.String(30), default="schedule")
    created_at = db.Column(db.DateTime, default=utcnow)
    completed_at = db.Column(db.DateTime)

    farm = db.relationship("Farm", back_populates="tasks")
    recommendation = db.relationship("Recommendation", back_populates="tasks")

    def title(self, language="en"):
        if language == "mr" and self.title_mr:
            return self.title_mr
        return self.title_en

    def detail(self, language="en"):
        if language == "mr" and self.detail_mr:
            return self.detail_mr
        return self.detail_en

    @property
    def is_done(self):
        return self.status == "done"
