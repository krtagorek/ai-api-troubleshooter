"""Real local RSA verification; all IdP key/revocation sources are mocked."""
import asyncio,json,time
from datetime import datetime,timezone,timedelta
from uuid import uuid4
import jwt,pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from diagnostic_contracts.identity import JWTAuthenticator,KeySnapshot,RevocationVerdict

class Keys:
    def __init__(self,key):self.key=key;self.stale=False
    async def snapshot(self):return KeySnapshot(self.key,datetime.now(timezone.utc)+timedelta(seconds=-1 if self.stale else 60))
class Revocations:
    allowed=True
    async def check(self,*args):return RevocationVerdict(self.allowed,datetime.now(timezone.utc)+timedelta(seconds=5))

def configured():
    private=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    public=json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()));public.update(kid='key-1',alg='RS256',use='sig')
    keys=Keys({'key-1':public});rev=Revocations();auth=JWTAuthenticator(issuer='https://identity.example.invalid',audience='troubleshooter',keys=keys,revocations=rev)
    return auth,private,keys,rev

def token(key,**changes):
    now=int(time.time());claims={'iss':'https://identity.example.invalid','aud':'troubleshooter','sub':str(uuid4()),'iat':now,'nbf':now,'exp':now+60,'jti':str(uuid4()),'roles':['global_admin'],'tenant_id':str(uuid4())};claims.update(changes)
    return jwt.encode(claims,key,algorithm='RS256',headers={'kid':'key-1'})

def test_signature_current_identity_rotation_and_revocation():
    async def run():
        a,k,keys,rev=configured();p=await a.authenticate(token(k));assert p is not None and await a.current(p)
        rev.allowed=False;assert not await a.current(p);rev.allowed=True
        keys.key={};assert not await a.current(p)
        assert await a.authenticate(token(k)) is None
    asyncio.run(run())

@pytest.mark.parametrize('changes',[{'iss':'https://wrong.invalid'},{'aud':'wrong'},{'exp':0},{'nbf':9999999999},{'sub':'invalid'},{'jti':''}])
def test_invalid_claims(changes):
    async def run():
        a,k,_,_=configured();assert await a.authenticate(token(k,**changes)) is None
    asyncio.run(run())

def test_forged_missing_stale_and_unconfigured_identity():
    async def run():
        a,k,keys,r=configured();other=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        assert await a.authenticate(token(other)) is None
        assert await a.authenticate('') is None
        keys.stale=True;assert await a.authenticate(token(k)) is None
        a.keys=None;assert await a.authenticate(token(k)) is None
        from registry.authorization import Principal
        assert not await a.current(Principal(uuid4()))
    asyncio.run(run())


def test_configuration_missing_providers_and_production_demo_rejection(monkeypatch):
    from backend.auth import authentication_from_env
    monkeypatch.setenv('APP_ENV','production');monkeypatch.setenv('DEMO_AUTH_ENABLED','true')
    with pytest.raises(ValueError,match='development'):authentication_from_env()
    monkeypatch.setenv('DEMO_AUTH_ENABLED','false');monkeypatch.setenv('IDENTITY_ISSUER','https://identity.example.invalid');monkeypatch.setenv('IDENTITY_AUDIENCE','test')
    with pytest.raises(ValueError,match='providers'):authentication_from_env()
    with pytest.raises(ValueError):JWTAuthenticator(issuer='http://unsafe.invalid',audience='test')
