# Toontown: Dearchipelagoified
A project aimed to remove Archipelago features from TTAP/TTCL, for developing your own projects using the source without leftovers from other projects.

This source is built on the foundation of Toontown Offline's Toontown School House's source code.
Toontown School House is a course dedicated to teaching members of the Toontown community how to develop for the game. For more information, head over to [this](https://www.reddit.com/r/Toontown/comments/doszgg/toontown_school_house_learn_to_develop_for/) Reddit post.


This version of Toontown makes modifications to the base game to introduce 
gameplay tweaks to make the experience quicker, more satisfying, and more solo friendly. This game also has support for hosting
and joining mini-servers if you still wish to play with friends! 

# Source Code
This source code is based on a March 2019 fork of Toontown Offline v1.0.0.0 used for Toontown School House. 
It has been stripped of all Toontown Offline exclusive features, save one. The brand new Magic Words system made for 
Toontown Offline has been left alone, and upgraded to the most recent build. This feature will allow users to easily navigate around Toontown without any hassle.

On top of that, this source code has also been updated to Python 3, utilizing a more modern version of Panda3D. 

Credits:
* [The Toontown Offline Team](https://ttoffline.com) for the foundation of this codebase (Toontown Schoolhouse)
* [The Corporate Clash Crew](https://corporateclash.net) for toon models, some various textures, and assistance with implementing v1.2.8 craning
* Polygon for making the Corporate Clash toon models
* [Open Toontown](https://github.com/open-toontown) for providing a great reference for a Toontown codebase ported to Python 3 and the HD Mickey Font
* Toontown Infinite for Bossbot HQ suit paths
* [Astron](https://github.com/Astron/Astron)
* [Panda3D](https://github.com/panda3d/panda3d)  (More specifically, [Open Toontown's fork of Panda3D](https://github.com/open-toontown/panda3d))
* [libotp-nametags](https://github.com/loblao/libotp-nametags)
* [Ben Briggs](https://www.youtube.com/@benbriggsmusic) for the dripstinct music.
* [Project Bikehorn](https://github.com/toonjoey/toontown-project-bikehorn) created by toonjoey, for the textures made game ready from the Pandora leak.
* Reverse-engineered Toontown Online client/server source code is property of The Walt Disney Company.

# Getting Started

At this time, Windows is the only supported platform. For other platforms, please see [Running From Source.](#running-from-source)

# Running from source

## Panda3D
This source code requires a specific version of Panda3D to run.

### Windows

Please download the latest engine build from [here.](https://github.com/toontown-archipelago/panda3d/releases/latest)

### Other

At this time Toontown: Dearchipelagoified only supports Windows.
To run on other platforms you will need to build the engine. 
This is an advanced use-case and is unsupported.
To get started, please see the build instructions [here.](https://github.com/toontown-archipelago/panda3d)

## Starting the game

Once Panda3D is installed, please find your systems launch directory.
- Windows: `win32`
- Mac: `darwin`
- Linux: `linux`

Then run the following scripts in order:
- `start_astron_server`
- `start_uberdog_server`
- `start_ai_server`
- `start_game`

## Common Issues/FAQ

### I set up the server and everything is running fine. I can connect to my own server but my friends can't. Why?

If you are hosting a Mini-Server, you **must** port forward to allow incoming connections on port `7198`.
There are two ways to accomplish this:

- Port forward the port `7198` in your router's settings.
- Use a third party program (such as Hamachi) to emulate a LAN connection over the internet.

As router settings are wildly different, I cannot provide a tutorial on how to do this on this README for your specific
router. However, the process is pretty straight forward assuming you have access to your router's settings. 
You should be able to figure it out with a bit of research on Google.


### I launched the game and I am getting the error: The system cannot find the path specified

You did not do the `PPYTHON_PATH` step correctly from before. Double check that Panda3D is installed at the directory
located in `PPYTHON_PATH` and try again.


### I logged in and I have no gags and can't access the Toon HQ.... why can't I play?

Check your 
book and look at the spellbook page to see what you can do. `~maxtoon` will put your toon in a state where you can do 
anything in the game with no restrictions.


### I was playing and my game crashed :(

Toontown: Dearchipelagoified is currently in an early alpha build so many issues are expected to be present. If you found a
crash/bug, feel free to create an Issue on the GitHub page for the repository. Developers/contributors
use this as a "todo list". If you choose to do this, try and be as descriptive as possible on what caused the crash, and 
any sort of possible steps that can be taken to reproduce it.


### I was playing and the district reset :(

Similarly to a game crash, sometimes the district can crash. Follow the same steps as the previous point.
