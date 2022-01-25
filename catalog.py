#!/usr/bin/python3 -u
# -*- coding: utf-8 -*-
'''
Archivo que implementa las clases correspondientes al servicio de catálogo.
'''

import uuid
import sys
import threading
import random
import Ice # pylint: disable=import-error,wrong-import-position
import topics
from catalog_updates import CatalogUpdates
from stream_announcements import StreamAnnouncements
from discover import Discover
from utils import CatalogDB, read_tags_db, write_tags_db
from media import Media, MediaInfo, MediaDB
Ice.loadSlice('iceflix.ice')
import IceFlix # pylint: disable=import-error,wrong-import-position

class Catalog(IceFlix.MediaCatalog): # pylint: disable = too-many-instance-attributes
    '''Clase que implementa la interfaz de IceFlix para el catálogo.'''
    def __init__(self):
        self._srv_id = str(uuid.uuid4())
        self.tags_db = 'tags_' + self._srv_id + '.json'
        self.catalog = CatalogDB(self._srv_id + '.db')
        self.discover_subscriber = None
        self.stream_announcements_subscriber = None
        self.catalog_updates_subscriber = None
        self.media_with_proxy = {}
        self.is_up_to_date = False
        self.up_to_date_timer = None
        self.prx = None

    @property
    def current_database(self):
        """Get current users db."""
        # No tiene sentido devolver una lista.
        # El json de las tags se puede devolver completo, y así se repite por cada medio.
        return [MediaDB(
            media_id, self.catalog.get_name_by_id(media_id),
            read_tags_db(self.tags_db)) for media_id in self.catalog.get_all()]
    @property
    def service_id(self):
        """Get instance ID."""
        return self._srv_id

    def getTile(self, mediaId, userToken, current=None): # pylint: disable=invalid-name, unused-argument
        '''Obtiene toda la información en forma de objeto Media dado un ID.'''
        if not self.catalog.is_in_catalog(mediaId):
            raise IceFlix.WrongMediaId(mediaId)

        if mediaId not in self.media_with_proxy:
            raise IceFlix.TemporaryUnavailable

        # La interfaz no contempla la posibilidad de que no exista un main service disponible.
        # Lo mismo ocurre para auth. Se podría reciclar Temporary Unavailable
        # pero en otros métodos no se puede, así que aquí tampoco se hace.
        main_service = random.choice(list(self.discover_subscriber.main_services.values()))
        auth_service = main_service.getAuthenticator()

        username = auth_service.whois(userToken) # Implicitly throws Unauthorized

        tags_db = read_tags_db(self.tags_db)
        tag_list = []

        if username in tags_db and mediaId in tags_db[username]:
            tag_list = tags_db[username][mediaId]

        # Cambiar la forma en la que obtiene el provider si es necesario
        return Media(mediaId, self.media_with_proxy[mediaId][-1],
                     MediaInfo(self.catalog.get_name_by_id(mediaId), tag_list))

    def getTilesByName(self, name, exact, current=None): # pylint: disable=invalid-name, unused-argument
        '''Obtiene el ID de un medio dado un nombre.'''
        tiles_list = self.catalog.get_id_by_name(name, exact)
        if tiles_list:
            return tiles_list
        return []

    def getTilesByTags(self, tags, includeAllTags, userToken, current=None): # pylint: disable=invalid-name, unused-argument
        '''Obtiene el ID de un medio dada una lista de tags.'''
        # La interfaz no contempla la posibilidad de que no exista un main service disponible.
        # Lo mismo ocurre para auth. Se podría reciclar Temporary Unavailable
        # pero en otros métodos no se puede, así que aquí tampoco se hace.
        main_service = random.choice(list(self.discover_subscriber.main_services.values()))
        auth_service = main_service.getAuthenticator()

        user = auth_service.whois(userToken) # Implicitly throws Unauthorized

        tags_db = read_tags_db(self.tags_db)
        tiles_list = []
        for media in tags_db[user]:
            if includeAllTags and all(tag in tags_db[user][media] for tag in tags):
                tiles_list.append(media)
            elif not includeAllTags and any(tag in tags_db[user][media] for tag in tags):
                tiles_list.append(media)

        return tiles_list

    def addTags(self, media_id, tags, user_token, current=None): # pylint: disable=invalid-name, unused-argument
        '''Añade tags a un medio.'''
        # La interfaz no contempla la posibilidad de que no exista un main service disponible.
        # Lo mismo ocurre para auth. Se podría reciclar Temporary Unavailable
        # pero en otros métodos no se puede, así que aquí tampoco se hace.
        main_service = random.choice(list(self.discover_subscriber.main_services.values()))
        auth_service = main_service.getAuthenticator()

        username = auth_service.whois(user_token) # Implicitly throws Unauthorized

        if not self.catalog.is_in_catalog(media_id):
            raise IceFlix.WrongMediaId(media_id)

        tags_db = read_tags_db(self.tags_db)

        if username in tags_db and media_id in tags_db[username]:
            for tag in tags:
                tags_db[username][media_id].append(tag)
        else:
            tags_dic = {}
            tags_dic[media_id] = tags
            tags_db[username] = tags_dic

        write_tags_db(tags_db, self.tags_db)
        self.catalog_updates_subscriber.publisher.addTags(media_id, username, self.service_id)

    def removeTags(self, media_id, tags, user_token, current=None): # pylint: disable=invalid-name, unused-argument
        '''Elimina tags de un medio.'''
        # La interfaz no contempla la posibilidad de que no exista un main service disponible.
        # Lo mismo ocurre para auth. Se podría reciclar Temporary Unavailable
        # pero en otros métodos no se puede, así que aquí tampoco se hace.
        main_service = random.choice(list(self.discover_subscriber.main_services.values()))
        auth_service = main_service.getAuthenticator()

        user = auth_service.whois(user_token) # Implicitly throws Unauthorized

        if not self.catalog.is_in_catalog(media_id):
            raise IceFlix.WrongMediaId(media_id)

        tags_db = read_tags_db(self.tags_db)

        if user in tags_db and media_id in tags_db[user]:
            tags_db[user][media_id] = [tag for tag in tags_db[user][media_id] if tag not in tags]
        write_tags_db(tags_db, self.tags_db)
        self.catalog_updates_subscriber.publisher.removeTags(media_id, tags, user, self.service_id)

    def renameTile(self, media_id, name, admin_token, current=None): # pylint: disable=invalid-name, unused-argument
        '''Renombra un medio.'''
        # La interfaz no contempla la posibilidad de que no exista un main service disponible.
        main_service = random.choice(list(self.discover_subscriber.main_services.values()))

        if not main_service.isAdmin(admin_token):
            raise IceFlix.Unauthorized

        if not self.catalog.is_in_catalog(media_id):
            raise IceFlix.WrongMediaId(media_id)

        self.catalog.rename_media(media_id, name)
        self.catalog_updates_subscriber.publisher.renameTile(media_id, name, self.service_id)

    def updateDB(self, catalog_database, srv_id, current=None): # pylint: disable=invalid-name, unused-argument
        '''Send update to the new service detected'''
        if self.service_id == srv_id:
            return
        if not self.is_up_to_date:
            if srv_id not in self.discover_subscriber.catalog_services.keys():
                raise IceFlix.UnknownService
            print(f'\n[CATALOG SERVICE][INFO] Update received from {srv_id}.')
            if self.up_to_date_timer.is_alive():
                self.up_to_date_timer.cancel()
            # Sólo guarda la config de tags desde el primer elemento ya que es repetida en todos.
            if catalog_database:
                write_tags_db(catalog_database[0].tagsPerUser, self.tags_db)
            # Reinicia la base de datos que tenga para evitar inconsistencias.
            self.catalog.drop_table()
            self.catalog.create_table()
            for media in catalog_database:
                self.catalog.add_media(media.mediaId, media.name)
            self.is_up_to_date = True
            self.discover_subscriber.publisher.announce(self.prx, self.service_id)

class CatalogServer(Ice.Application):
    '''Clase que implementa el servicio de catálogo.'''
    def run(self, argv): # pylint: disable=arguments-differ, unused-argument
        broker = self.communicator()
        catalog_adapter = broker.createObjectAdapterWithEndpoints("CatalogAdapter", "tcp")
        catalog_adapter.activate()

        servant = Catalog()
        servant_proxy = catalog_adapter.addWithUUID(servant)
        servant.prx = servant_proxy

        # Discover topic
        discover_topic = topics.getTopic(topics.getTopicManager(self.communicator()), 'discover')
        servant.discover_subscriber = Discover(servant, servant_proxy)
        discover_subscriber_proxy = catalog_adapter.addWithUUID(servant.discover_subscriber)
        discover_topic.subscribeAndGetPublisher({}, discover_subscriber_proxy)
        publisher = discover_topic.getPublisher()
        publisher = IceFlix.ServiceAnnouncementsPrx.uncheckedCast(publisher)
        servant.discover_subscriber.publisher = publisher
        servant.discover_subscriber.announce_timer = threading.Timer(
            10.0+random.uniform(0.0, 2.0), servant.discover_subscriber.publisher.announce,
            args=[servant_proxy, servant.service_id])

        # Stream announcements topic
        stream_announcements_topic = topics.getTopic(
            topics.getTopicManager(self.communicator()), 'streamannouncements')
        servant.stream_announcements_subscriber = StreamAnnouncements(servant)
        stream_announcements_subscriber_proxy = catalog_adapter.addWithUUID(
            servant.stream_announcements_subscriber)
        stream_announcements_topic.subscribeAndGetPublisher(
            {}, stream_announcements_subscriber_proxy)

        # Catalog updates topic
        catalog_updates_topic = topics.getTopic(
            topics.getTopicManager(self.communicator()), 'catalogupdates')
        servant.catalog_updates_subscriber = CatalogUpdates(servant)
        catalog_updates_subscriber_proxy = catalog_adapter.addWithUUID(
            servant.catalog_updates_subscriber)
        catalog_updates_topic.subscribeAndGetPublisher({}, catalog_updates_subscriber_proxy)
        publisher = catalog_updates_topic.getPublisher()
        publisher = IceFlix.CatalogUpdatesPrx.uncheckedCast(publisher)
        servant.catalog_updates_subscriber.publisher = publisher

        servant.discover_subscriber.publisher.newService(servant_proxy, servant.service_id)

        def set_up_to_date():
            print(
                "\n[CATALOG SERVICE][INFO] No update event received. " +
                "Assuming I'm the first of my kind...")
            print(f'\n[CATALOG SERVICE][INFO] My ID is {servant.service_id}')
            servant.is_up_to_date = True
            servant.catalog = CatalogDB('catalog.db')
            servant.catalog.create_table()
            servant.tags_db = 'tagsDB.json'
            servant.discover_subscriber.publisher.announce(servant_proxy, servant.service_id)

        servant.up_to_date_timer = threading.Timer(3.0, set_up_to_date)
        servant.up_to_date_timer.start()

        print("\n[CATALOG SERVICE][INFO] Servicio iniciado.")

        self.shutdownOnInterrupt()
        broker.waitForShutdown()

        discover_topic.unsubscribe(discover_subscriber_proxy)

        return 0


sys.exit(CatalogServer().main(sys.argv))
