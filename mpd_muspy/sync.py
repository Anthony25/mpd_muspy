#!/usr/bin/python
# Author: Anthony Ruhier

import os
import mpd
import multiprocessing
from multiprocessing.managers import BaseManager
from . import _current_dir
from .artist_db import Artist_db
from .muspy_api import Muspy_api
from .tools import chunks, mpd_get_artists
from config import ARTISTS_JSON

ARTISTS_JSON = os.path.join(_current_dir, ARTISTS_JSON)
# After multiple tests, it appears that this value is the best compromise to
# avoid HTTP error 400 with the musicbrainz api
NB_MULTIPROCESS = 3


class SyncManager(BaseManager):
    pass

SyncManager.register('Artist_db', Artist_db)
SyncManager.register('Muspy_api', Muspy_api)


def update_artists_from_muspy(artist_db):
    """
    Update the uploaded state of artists from the ones already on the muspy
    account.

    :param artist_db: database of local artists
    """
    local_artists = artist_db.get_artists(group_by="uploaded")
    mapi = Muspy_api()
    muspy_artists = mapi.get_artists()
    try:
        non_uploaded_artists = local_artists[False]
        for ma in muspy_artists:
            try:
                ma_name = ma["name"]
                if ma_name in non_uploaded_artists:
                    artist_db.mark_as_uploaded(ma_name)
                if artist_db.get_mbid(ma_name) is None:
                    artist_db.set_mbid(ma_name, ma["mbid"])
            except KeyError:
                pass
    except IndexError:
        pass
    artist_db.save()


def process_task(artists, artists_nb, artist_db, lock, counter):
    """
    Function launched by each process

    Add artists on muspy and marks it in the artists database.

    :param artists: list of artists split for this process
    :type artists: list
    :param artists_nb: total artists to upload. Different of len(artists), here
                       it is the total number of artists of all processes.
    :type artists_nb: int
    :param artist_db: database of artists, in the shared memory
    :type artist_db: SyncManager.Artist_db()
    :param lock: lock shared between the processes
    :type lock: multiprocessing.Lock
    :param counter: integer in the shared memory
    :type counter: multiprocessing.Value("i")
    """
    muspy_api = Muspy_api()
    for artist in artists:
        error = ""
        try:
            muspy_api.add_artist(artist)
            with lock:
                artist_db.mark_as_uploaded(artist)
                artist_db.save()
        except Exception as e:
            error = "Error: " + str(e)
            pass
        finally:
            with lock:
                counter.value += 1
            print("[", counter.value, "/", artists_nb, "]:", artist.title())
            if error:
                print(error)


def start_pool(non_uploaded_artists, artist_db):
    """
    Initialize the synchronization in several process

    :param non_uploaded_artists: list of artists name to upload
    :type non_uploaded_artists: list
    :param artist_db: Artist_db() object in the shared memory
    :type artist_db: SyncManager.Artist_db
    """
    manager = multiprocessing.Manager()
    lock = manager.Lock()
    counter = manager.Value("i", 0)
    artists_nb = len(non_uploaded_artists)
    artists_nb_by_split = int(artists_nb / NB_MULTIPROCESS)
    pool = multiprocessing.Pool()
    for l in chunks(non_uploaded_artists, artists_nb_by_split):
        pool.apply_async(
            process_task,
            kwds={"artists": l, "artists_nb": artists_nb,
                  "artist_db": artist_db, "lock": lock, "counter": counter}
        )
    pool.close()
    pool.join()


def pre_sync(artist_db):
    mpdclient = mpd.MPDClient()
    print("Get mpd artists...")
    artists = mpd_get_artists(mpdclient)
    artists_removed, artists_added = artist_db.merge(artists)

    # Update the uploaded status of artists in the db with the muspy account
    print("Pre-synchronization with muspy...")
    update_artists_from_muspy(artist_db)
    artist_db.save()

    non_uploaded_artists = artist_db.get_artists(uploaded=False)
    print()
    print(len(non_uploaded_artists), "artist(s) non uploaded on muspy")
    print(len(artists_added), "artist(s) added")
    print(len(artists_removed), "artist(s) removed")

    return non_uploaded_artists


def run():
    process_manager = SyncManager()
    process_manager.start()
    artist_db = process_manager.Artist_db(jsonpath=ARTISTS_JSON)
    non_uploaded_artists = pre_sync(artist_db)

    print("\n   Start syncing\n =================\n")
    start_pool(non_uploaded_artists, artist_db)
    print("Done: ",
          len(non_uploaded_artists) -
          len(artist_db.get_artists(uploaded=False)),
          "artist(s) updated")
