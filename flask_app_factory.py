import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path

import connexion
from flask_login import login_required
from webassets.loaders import PythonLoader as PythonAssetsLoader

from app.controllers.admin.context_processors import users_helpers
from app.controllers.admin.views import admin_bp, auth_bp
from app.utils import ContextualFilter, SaferProxyFix
from app.utils.gateways import register_senders

from . import assets
from .extensions import cache, jsglue, login_manager, mail, db, cors, assets as assets_env, celery
from .handlers import (load_user, load_user_from_request, determine_bad_password,
                       swagger_error_handler, handle_custom_error, handle_not_found,
                       handle_every_exception)
from .views import site_map, share
from .response import Error


def create_app(config=None, config_overrides=None):
    # connexion app (wrapper)
    k24 = connexion.App('app', specification_dir='../swagger/')
    k24.add_api('api.yaml')

    # flask app
    app = k24.app
    app.static_folder = '../static'
    configure_app(app, config, config_overrides)

    app.wsgi_app = SaferProxyFix(app.wsgi_app)

    register_extensions(app)
    register_handlers(app)
    register_blueprints(app)
    register_views(app)
    register_context_processors(app)

    configure_celery(app)
    configure_logging(app)

    prepare_dirs(app)

    return app


def configure_app(app, config, config_overrides):
    if not config:
        if os.environ.get('KENGU24_CONFIG'):
            app.config.from_object(os.environ['KENGU24_CONFIG'])
        else:
            app.config.from_object('local_config.LocalConfig')
    else:
        app.config.from_object(config)

    if config_overrides:
        app.config.update(config_overrides)


def register_extensions(app):
    cache.init_app(app)
    jsglue.init_app(app)
    login_manager.init_app(app)
    mail.init_app(app)
    db.init_app(app)
    cors.init_app(app)

    assets_env.init_app(app)
    assets_loader = PythonAssetsLoader(assets)
    for name, bundle in assets_loader.load_bundles().items():
        assets_env.register(name, bundle)


def register_handlers(app):
    login_manager.user_loader(load_user)
    login_manager.request_loader(load_user_from_request)

    # app.before_request(determine_bad_password)
    app.after_request(swagger_error_handler)

    if not app.testing:
        app.register_error_handler(Error, handle_custom_error)
        app.register_error_handler(404, handle_not_found)
        app.register_error_handler(Exception, handle_every_exception)

    register_senders()


def register_blueprints(app):
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)


def register_views(app):
    app.add_url_rule('/sitemap', view_func=login_required(site_map))

    if app.debug:
        app.add_url_rule('/share/<path:filename>', view_func=share)

    # restrict access to Swagger UI views to authenticated users only
    swagger_views = ('swagger_json', 'swagger_ui_index', 'swagger_ui_static')
    for endpoint_name, view_func in app.view_functions.items():
        if endpoint_name.endswith(swagger_views):
            app.view_functions[endpoint_name] = login_required(view_func)


def register_context_processors(app):
    app.context_processor(users_helpers)


def configure_celery(app):
    celery.conf.update(app.config['CELERY'])

    TaskBase = celery.Task

    class ContextTask(TaskBase):
        abstract = True

        def __call__(self, *args, **kwargs):
            with app.app_context():
                return TaskBase.__call__(self, *args, **kwargs)

    celery.Task = ContextTask


def configure_logging(app):
    if not app.debug:
        file_handler = RotatingFileHandler(app.config['LOG_FILE'], maxBytes=10000)
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)s %(ip)s - %(uid)s - %(method)s %(url)s\n"
            "%(pathname)s:%(lineno)d]: %(funcName)s |\n"
            "%(message)s\n"
            "-------------------------------------------------------------------------------\n"
        )
        # file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        app.logger.addHandler(file_handler)
        app.logger.addFilter(ContextualFilter())


def prepare_dirs(app):
    share_dir = Path(app.config['SHARE_DIR'])

    if not share_dir.joinpath('barcodes').exists():
        share_dir.joinpath('barcodes').mkdir()

    if not share_dir.joinpath('stickers').exists():
        share_dir.joinpath('stickers').mkdir()

    if not share_dir.joinpath('documents').exists():
        share_dir.joinpath('documents').mkdir()

    if not share_dir.joinpath('tokens').exists():
        share_dir.joinpath('tokens').mkdir()
