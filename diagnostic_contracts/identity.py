"""Strict RS256 verification with injected fresh key/revocation authorities.

No discovery URLs from tokens, no network fetching, and no token persistence.
"""
from dataclasses import dataclass,field
from datetime import datetime,timezone,timedelta
from typing import Protocol
from uuid import UUID,uuid4
from urllib.parse import urlsplit
import jwt
import asyncio
import hashlib,json,copy
from jwt.algorithms import RSAAlgorithm
from registry.authorization import Principal

@dataclass(frozen=True)
class TrustedPrincipal(Principal):
    session_id: UUID=field(repr=False)

@dataclass(frozen=True)
class KeySnapshot:
    keys: dict
    valid_until: datetime

@dataclass(frozen=True)
class RevocationVerdict:
    allowed: bool
    valid_until: datetime

class KeyAuthority(Protocol):
    async def snapshot(self)->KeySnapshot: ...

class RevocationAuthority(Protocol):
    async def check(self,issuer,subject,jti,issued_at)->RevocationVerdict: ...

class JWTAuthenticator:
    def __init__(self,*,issuer,audience,keys=None,revocations=None,clock=None):
        parsed=urlsplit(issuer)
        if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('Explicit HTTPS issuer configuration required')
        if not audience or not isinstance(audience,str):raise ValueError('Explicit audience required')
        self.issuer,self.audience,self.keys,self.revocations=issuer,audience,keys,revocations
        self.clock=clock or (lambda:datetime.now(timezone.utc));self._sessions={}

    async def _revoked(self,claims):
        if self.revocations is None:return False
        verdict=await asyncio.wait_for(self.revocations.check(self.issuer,claims['sub'],claims['jti'],claims['iat']),timeout=2)
        now=self.clock()
        return (isinstance(verdict,RevocationVerdict) and verdict.allowed is True and
                now<verdict.valid_until<=now+timedelta(seconds=30))

    async def authenticate(self,token):
        try:
            if self.keys is None or self.revocations is None or not isinstance(token,str) or len(token)>16384:return None
            header=jwt.get_unverified_header(token)
            if header.get('alg')!='RS256' or not isinstance(header.get('kid'),str):return None
            if any(k in header for k in ('jku','jwk','x5u','crit')):return None
            snapshot=await asyncio.wait_for(self.keys.snapshot(),timeout=2);now=self.clock()
            if not isinstance(snapshot,KeySnapshot) or not now<snapshot.valid_until<=now+timedelta(minutes=5):return None
            jwk=copy.deepcopy(snapshot.keys.get(header['kid']))
            if not jwk or jwk.get('kty')!='RSA' or jwk.get('use')!='sig' or jwk.get('alg')!='RS256':return None
            key=RSAAlgorithm.from_jwk(jwk)
            if key.key_size<2048:return None
            claims=jwt.decode(token,key,algorithms=['RS256'],issuer=self.issuer,audience=self.audience,
                options={'require':['iss','aud','sub','exp','iat','nbf','jti']},leeway=0)
            if any(type(claims[k]) is not int for k in ('exp','iat','nbf')):return None
            if not claims['iat']<=now.timestamp()<claims['exp'] or claims['nbf']>now.timestamp():return None
            if not isinstance(claims['jti'],str) or not 1<=len(claims['jti'])<=128:return None
            subject=UUID(claims['sub'])
            if not await self._revoked(claims):return None
            self._sessions={k:v for k,v in self._sessions.items() if v['exp']>now.timestamp()}
            if len(self._sessions)>=10000:return None
            principal=TrustedPrincipal(subject,uuid4())
            # Retain only bounded authorization metadata, never JWT or claims roles.
            self._sessions[principal.session_id]={k:claims[k] for k in ('sub','exp','iat','jti')}
            self._sessions[principal.session_id].update(kid=header['kid'],key_digest=hashlib.sha256(json.dumps(jwk,sort_keys=True).encode()).hexdigest())
            return principal
        except Exception:return None

    async def current(self,principal):
        try:
            if not isinstance(principal,TrustedPrincipal):return False
            claims=self._sessions.get(principal.session_id)
            if not claims or str(principal.subject_id)!=claims['sub'] or self.clock().timestamp()>=claims['exp']:return False
            snapshot=await asyncio.wait_for(self.keys.snapshot(),timeout=2);now=self.clock()
            if not isinstance(snapshot,KeySnapshot) or not now<snapshot.valid_until<=now+timedelta(minutes=5):return False
            key=snapshot.keys.get(claims['kid'])
            if not key or hashlib.sha256(json.dumps(key,sort_keys=True).encode()).hexdigest()!=claims['key_digest']:return False
            return await self._revoked(claims)
        except Exception:return False

    async def authorization_expiry(self,principal):
        """Fresh bounded authorization lease, never an independently usable permit."""
        try:
            if not await self.current(principal):return None
            claims=self._sessions[principal.session_id]
            verdict=await asyncio.wait_for(self.revocations.check(self.issuer,claims['sub'],claims['jti'],claims['iat']),2)
            snapshot=await asyncio.wait_for(self.keys.snapshot(),2)
            now=self.clock()
            if (not isinstance(verdict,RevocationVerdict) or verdict.allowed is not True or
                not now<verdict.valid_until<=now+timedelta(seconds=30) or
                not isinstance(snapshot,KeySnapshot) or not now<snapshot.valid_until<=now+timedelta(minutes=5)):
                return None
            key=snapshot.keys.get(claims['kid'])
            if not key or hashlib.sha256(json.dumps(key,sort_keys=True).encode()).hexdigest()!=claims['key_digest']:return None
            return min(datetime.fromtimestamp(claims['exp'],timezone.utc),verdict.valid_until,snapshot.valid_until)
        except Exception:return None
