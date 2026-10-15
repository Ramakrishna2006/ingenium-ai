def register_blueprints(app):
    from app.routes.admin import bp as admin_bp
    from app.routes.analytics import bp as analytics_bp
    from app.routes.api import bp as api_bp
    from app.routes.applications import bp as applications_bp
    from app.routes.auth import bp as auth_bp
    from app.routes.candidate_portal import bp as candidate_bp
    from app.routes.candidates import bp as candidates_bp
    from app.routes.careers import bp as careers_bp
    from app.routes.employees import bp as employees_bp
    from app.routes.files import bp as files_bp
    from app.routes.interviews import bp as interviews_bp
    from app.routes.jobs import bp as jobs_bp
    from app.routes.main import bp as main_bp
    from app.routes.offers import bp as offers_bp
    from app.routes.onboarding import bp as onboarding_bp
    from app.routes.portal import bp as portal_bp

    for bp in (main_bp, auth_bp, careers_bp, candidate_bp, jobs_bp, candidates_bp, applications_bp,
               interviews_bp, offers_bp, employees_bp, onboarding_bp, portal_bp, analytics_bp, admin_bp,
               files_bp, api_bp):
        app.register_blueprint(bp)
