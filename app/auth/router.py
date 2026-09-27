"""HTTP endpoints for registration and login."""

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth.dependencies import get_auth_service, limit_auth_attempts
from app.auth.schemas import LoginRequest, RegisterRequest, TokenResponse, UserResponse
from app.auth.service import AuthService, EmailAlreadyRegistered, InvalidCredentials
from app.core.config import settings
from app.core.errors import StorageError

# The limit is declared on the router, so it covers every endpoint in this file,
# including any added later. Both current endpoints run bcrypt.
router = APIRouter(
    prefix="/auth",
    tags=["auth"],
    dependencies=[Depends(limit_auth_attempts)],
)


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(
    request: RegisterRequest,
    service: AuthService = Depends(get_auth_service),
) -> UserResponse:
    try:
        user = service.register(
            organization_name=request.organization_name,
            email=request.email,
            password=request.password,
        )
    except EmailAlreadyRegistered:
        # 409 Conflict, not 400. The request is well formed; it conflicts with
        # state that already exists. And not 503 either - retrying cannot help.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists",
        )
    except StorageError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Storage is unavailable",
        )

    # Outside the try: only the risky call belongs inside it. Everything here
    # runs on the success path.
    return UserResponse.model_validate(user)


@router.post("/login", response_model=TokenResponse)
def login(
    request: LoginRequest,
    service: AuthService = Depends(get_auth_service),
) -> TokenResponse:
    try:
        token = service.authenticate(email=request.email, password=request.password)
    except InvalidCredentials:
        # 401 Unauthorized means "I do not know who you are". 403 would mean "I
        # know who you are and the answer is no" - a different situation.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            # The standard says a 401 must say how to authenticate.
            headers={"WWW-Authenticate": "Bearer"},
        )
    except StorageError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Storage is unavailable",
        )

    return TokenResponse(
        access_token=token,
        expires_in_seconds=settings.access_token_expire_minutes * 60,
    )
