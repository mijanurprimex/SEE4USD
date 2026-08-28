from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict

app = FastAPI(
    title="SEE4USD High-CPM API",
    version="1.0.0",
    description="Backend service managing geo-targeted payouts and verification."
)

# Enable CORS for local integration testing
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Tier 1 Regions configuration
TIER_1_COUNTRIES = {"US", "CH", "GB", "CA", "AU", "DE", "FR"}

# Mock Database Store
users_db: Dict[str, Dict] = {
    "usr_9982": {
        "user_id": "usr_9982",
        "country": "US",
        "tier_level": 1,
        "balance_usd": 12.50,
        "completed_ads": []
    }
}

ads_db = [
    {"id": "ad_101", "title": "Swiss Private Bank Tech App Demo", "duration": 15, "payout": 0.35, "allowed_country": "CH"},
    {"id": "ad_102", "title": "US FinTech Streaming Platform", "duration": 30, "payout": 0.50, "allowed_country": "US"},
    {"id": "ad_103", "title": "UK Consumer E-Commerce Survey", "duration": 20, "payout": 0.25, "allowed_country": "GB"},
    {"id": "ad_104", "title": "Germany Electric Vehicle Promo", "duration": 15, "payout": 0.30, "allowed_country": "DE"},
]

# Request Schemas
class AdVerifyRequest(BaseModel):
    user_id: str
    ad_id: str
    watch_duration: int
    token: str
    country: str

# Endpoints

@app.get("/api/v1/user/dashboard")
def get_user_dashboard(user_id: str, country: str = "US"):
    if user_id not in users_db:
        raise HTTPException(status_code=440, detail="User not found")
    
    user = users_db[user_id]
    # Update tier dynamically based on geo location header/param
    is_tier1 = country in TIER_1_COUNTRIES
    user["tier_level"] = 1 if is_tier1 else 2
    user["country"] = country

    return {
        "user_id": user["user_id"],
        "balance_usd": user["balance_usd"],
        "tier_level": user["tier_level"],
        "is_tier1_verified": is_tier1,
        "country": country
    }

@app.get("/api/v1/ads")
def get_ads(country: str = "US"):
    """
    Returns geo-targeted high-CPM ads based on user location.
    If targeted country matches, premium payout applies.
    """
    if country not in TIER_1_COUNTRIES:
        # Lower rate multiplier if accessed outside Tier-1 regions
        return [
            {**ad, "payout": round(ad["payout"] * 0.20, 2)} 
            for ad in ads_db
        ]
    
    # Return matched or general high-CPM inventory
    return [ad for ad in ads_db if ad["allowed_country"] in (country, "US")]

@app.post("/api/v1/ads/verify")
def verify_ad_claim(payload: AdVerifyRequest):
    """
    Anti-Fraud verification endpoint enforcing minimal watch-duration criteria.
    """
    if payload.user_id not in users_db:
        raise HTTPException(status_code=404, detail="User not found")
        
    ad = next((a for a in ads_db if a["id"] == payload.ad_id), None)
    if not ad:
        raise HTTPException(status_code=400, detail="Invalid Ad ID")

    # Verification Rules
    if payload.watch_duration < ad["duration"]:
        return {
            "status": "failed", 
            "reason": "Anti-Cheat Enforcement: Time elapsed on active window was insufficient."
        }

    # Calculate actual payout based on country tier status
    payout = ad["payout"]
    if payload.country not in TIER_1_COUNTRIES:
        payout = round(payout * 0.20, 2)

    # Credit balance
    users_db[payload.user_id]["balance_usd"] += payout
    users_db[payload.user_id]["completed_ads"].append(payload.ad_id)

    return {
        "status": "success",
        "credited_amount": payout,
        "new_balance": users_db[payload.user_id]["balance_usd"]
    }