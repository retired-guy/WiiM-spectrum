import numpy as np
import time, sys, math
import pygame
import io
from datetime import datetime
import requests
import xmltodict
import upnpclient
import textwrap
from collections import deque
from src.utils import Button
from matplotlib import cm
from threading import Thread

class Spectrum_Visualizer:
    """
    The Spectrum_Visualizer visualizes spectral FFT data using a simple PyGame GUI
    """
    def __init__(self, ear):
        self.wiim_ip = ear.wiim_ip

        # UPnP Device
        self.dev = upnpclient.Device(f"http://{self.wiim_ip}:49152/description.xml")

        thread = Thread(target=self.get_nowplaying)
        thread.daemon = True
        thread.start()
        
        self.plot_audio_history = False
        self.ear = ear

        self.HEIGHT  = self.ear.height
        window_ratio = self.ear.window_ratio

        self.HEIGHT = round(self.HEIGHT)
        self.WIDTH  = round(window_ratio*self.HEIGHT)
        print("ratio:",window_ratio,"HEIGHT",self.HEIGHT,"WIDTH:", round(window_ratio*self.HEIGHT))
        self.y_ext = [round(0.05*self.HEIGHT), self.HEIGHT/2]
        self.cm = cm.plasma
        self.TEXTCOLOR = (200,200,200)
        self.image = None
        self.artist = ""
        self.album = ""
        self.title = ""
        self.metatxt = ""
        self.DEFAULT_IMAGE_SIZE = (self.HEIGHT, self.HEIGHT)
        self.DEFAULT_IMAGE_POSITION = (0, 0)
        self.wrapper = textwrap.TextWrapper(width=50)
        self.Playing = False

        #self.cm = cm.inferno

        self.toggle_history_mode()

        self.add_slow_bars = 1
        self.add_fast_bars = 1
        self.slow_bar_thickness = max(0.00002*self.HEIGHT, 1.25 / self.ear.n_frequency_bins)
        self.tag_every_n_bins = max(1,round(5 * (self.ear.n_frequency_bins / 51))) # Occasionally display Hz tags on the x-axis

        self.fast_bar_colors = [list((255*np.array(self.cm(i))[:3]).astype(int)) for i in np.linspace(0,255,self.ear.n_frequency_bins).astype(int)]
        self.slow_bar_colors = [list(np.clip((255*3.5*np.array(self.cm(i))[:3]).astype(int) , 0, 255)) for i in np.linspace(0,255,self.ear.n_frequency_bins).astype(int)]
        self.fast_bar_colors = self.fast_bar_colors[::-1]
        self.slow_bar_colors = self.slow_bar_colors[::-1]

        self.slow_features = [0]*self.ear.n_frequency_bins
        self.frequency_bin_max_energies  = np.zeros(self.ear.n_frequency_bins)
        self.frequency_bin_energies = self.ear.frequency_bin_energies
        self.bin_text_tags, self.bin_rectangles = [], []

        #Fixed init params:
        self.start_time = None
        self.vis_steps  = 0
        self.fps_interval = 10
        self.fps = 0
        self._is_running = False

    def show_clock(self):

        self.screen.fill((self.bg_color,self.bg_color,self.bg_color))
        now = datetime.now()
        t = now.strftime("%H:%M")
        text = self.fontHuge.render(t, 1, (155, 155, 155))
        self.screen.blit(text, (300, 100))
        t = now.strftime("%a %b %d, %Y")
        text = self.fontLarge.render(t, 1, (155, 155, 155))
        self.screen.blit(text, (500, 350))
        pygame.display.update()

    def get_nowplaying(self):
        old_title = ""
    
        while True:
            time.sleep(1)
            if not self.Playing:
                continue
            try:
                obj = self.dev.AVTransport.GetInfoEx(InstanceID=0)
                transportstate = obj['CurrentTransportState']
                if transportstate != 'PLAYING':
                    self.Playing = False
                    continue

                self.Playing = True
                meta = obj['TrackMetaData']
                data = xmltodict.parse(meta)["DIDL-Lite"]["item"]
                title = data['dc:title'][:100]
                if title != old_title:
                    self.update_track_info(data)
                    old_title = title
                    self.fetch_album_art(data)
            except Exception as e:
                print(e)

    def update_track_info(self,data):
        self.artist = ""
        self.album = ""
        self.title = ""
        self.metatxt = ""

        self.title = data['dc:title'][:100]
        try:
            self.artist = data.get('upnp:artist', '')[:100]
        except:
            pass
        try:
            quality = int(data.get('song:quality', 0))
            rate = int(data.get('song:rate_hz', 0)) / 1000.0
            depth = int(data.get('song:format_s', 0))
            try:
                actualQuality = data.get('song:actualQuality', '')
                if actualQuality == "HD":
                    depth = 16
                if depth > 24:
                    depth = 24
            except:
                depth = 16
    
            try:
                self.album = data.get('upnp:album', '')[:100]
                if not self.album:
                    self.album = data.get('dc:subtitle', '')[:100]
            except:
                self.album = data.get('dc:subtitle', '')[:100]
    
            bitrate = f"{int(data.get('song:bitrate', 0))} kbps"
            self.metatxt = f"{depth} bits / {rate} kHz {bitrate}"
        except Exception as e:
            print(e)    

    def fetch_album_art(self,data):
        try:
            arturl = data["upnp:albumArtURI"]
            if isinstance(arturl, dict):
                arturl = arturl["#text"]
            r = requests.get(arturl, stream=True)
            img = io.BytesIO(r.content)
            self.image = pygame.image.load(img)
            self.image = pygame.transform.scale(self.image, self.DEFAULT_IMAGE_SIZE)
        except Exception as e:
            print(e)    

    def draw_text(self, surface, text, font, color, pos):
        lines = self.wrapper.wrap(text=text)
        y = pos[1]
        for line in lines:
            rendered_text = font.render(line, 1, color)
            surface.blit(rendered_text, (pos[0], y))
            y += 30
    
    def toggle_history_mode(self):

        if self.plot_audio_history:
            self.bg_color           = 0    #Background color
            self.decay_speed        = 0.10  #Vertical decay of slow bars
            self.inter_bar_distance = 0
            self.avg_energy_height  = 0.1125
            self.alpha_multiplier   = 0.995
            self.move_fraction      = 0.0099
            self.shrink_f           = 0.994

        else:
            self.bg_color           = 30
            self.decay_speed        = 0.06
            self.inter_bar_distance = int(0.2*(self.WIDTH-self.HEIGHT) / self.ear.n_frequency_bins)
            self.avg_energy_height  = 0.225

        self.bar_width = ((self.WIDTH-self.HEIGHT) / self.ear.n_frequency_bins) - self.inter_bar_distance

        #Configure the bars:
        self.slow_bars, self.fast_bars, self.bar_x_positions = [],[],[]
        for i in range(self.ear.n_frequency_bins):
            x = int(i* (self.WIDTH-self.HEIGHT) / self.ear.n_frequency_bins)
            fast_bar = [int(x), int(self.y_ext[0]), math.ceil(self.bar_width), None]
            slow_bar = [int(x), None, math.ceil(self.bar_width), None]
            self.bar_x_positions.append(x)
            self.fast_bars.append(fast_bar)
            self.slow_bars.append(slow_bar)

    def start(self):
        print("Starting spectrum visualizer...")
        pygame.init()
        self.screen = pygame.display.set_mode((self.WIDTH, self.HEIGHT),pygame.FULLSCREEN)
        self.screen.fill((self.bg_color,self.bg_color,self.bg_color))

        if self.plot_audio_history:
            self.screen.set_alpha(255)
            self.prev_screen = self.screen

        pygame.display.set_caption('Spectrum Analyzer -- (FFT-Peak: %05d)' %self.ear.strongest_frequency)
        self.bin_font = pygame.font.Font('freesansbold.ttf', round(0.05*self.HEIGHT))
        self.fps_font = pygame.font.Font('freesansbold.ttf', round(0.05*self.HEIGHT))
        
        self.fontSmall = pygame.font.Font('freesansbold.ttf', 20)
        self.fontLarge = pygame.font.Font('freesansbold.ttf', 32)
        self.fontHuge = pygame.font.Font('freesansbold.ttf', 256)
        
        for i in range(self.ear.n_frequency_bins):
            if i == 0 or i == (self.ear.n_frequency_bins - 1):
                continue
            if i % self.tag_every_n_bins == 0:
                f_centre = self.ear.frequency_bin_centres[i]
                text = self.bin_font.render('%d' %f_centre, True, (255, 255, 255) , (self.bg_color, self.bg_color, self.bg_color))
                textRect = text.get_rect()
                x = i*((self.WIDTH-self.HEIGHT) / self.ear.n_frequency_bins) + (self.bar_width - textRect.x)/2
                y = 0.98*self.HEIGHT
                textRect.center = (int(x+self.HEIGHT),int(y))
                self.bin_text_tags.append(text)
                self.bin_rectangles.append(textRect)

        self._is_running = True

        #Interactive components:
        self.button_height = round(0.05*self.HEIGHT)
        self.history_button  = Button(text="Toggle 2D/3D Mode", right=self.WIDTH, top=0, width=round(0.12*self.WIDTH), height=self.button_height)
        self.slow_bar_button = Button(text="Toggle Slow Bars", right=self.WIDTH, top=self.history_button.height, width=round(0.12*self.WIDTH), height=self.button_height)

    def stop(self):
        print("Stopping spectrum visualizer...")
        del self.fps_font
        del self.bin_font
        del self.screen
        del self.prev_screen
        pygame.quit()
        self._is_running = False

    def toggle_display(self):
        '''
        This function can be triggered to turn on/off the display
        '''
        if self._is_running: self.stop()
        else: self.start()

    def update(self):
        for event in pygame.event.get():
            if self.history_button.click():
                self.plot_audio_history = not self.plot_audio_history
                self.toggle_history_mode()
            if self.slow_bar_button.click():
                self.add_slow_bars = not self.add_slow_bars
                self.slow_features = [0]*self.ear.n_frequency_bins

        if np.min(self.ear.bin_mean_values) > 0:
            self.frequency_bin_energies = self.avg_energy_height * self.ear.frequency_bin_energies / self.ear.bin_mean_values
            self.Playing = True
        else:
            self.show_clock()
            self.Playing = False
            time.sleep(1)
            return

        if self.plot_audio_history:
            new_w, new_h = int((2+self.shrink_f)/3*self.WIDTH), int(self.shrink_f*self.HEIGHT)
            #new_w, new_h = int(self.shrink_f*self.WIDTH), int(self.shrink_f*self.HEIGHT)

            horizontal_pixel_difference = self.WIDTH - new_w
            prev_screen = pygame.transform.scale(self.prev_screen, (new_w, new_h))

        self.screen.fill((self.bg_color,self.bg_color,self.bg_color))

        if self.plot_audio_history:
            new_pos = int(self.move_fraction*self.WIDTH - (0.0133*self.WIDTH)), int(self.move_fraction*self.HEIGHT)
            self.screen.blit(pygame.transform.rotate(prev_screen, 180), new_pos)

        if self.start_time is None:
           self.start_time = time.time()

        self.vis_steps += 1

        if self.vis_steps%self.fps_interval == 0:
            self.fps = self.fps_interval / (time.time()-self.start_time)
            self.start_time = time.time()

        #self.text = self.fps_font.render('Fps: %.1f' %(self.fps), True, (255, 255, 255) , (self.bg_color, self.bg_color, self.bg_color))
        #self.textRect = self.text.get_rect()
        #self.textRect.x, self.textRect.y = round(0.015*self.WIDTH), round(0.03*self.HEIGHT)
        #pygame.display.set_caption('Spectrum Analyzer -- (FFT-Peak: %05d Hz)' %self.ear.strongest_frequency)
        self.plot_bars()
        
        if self.image:
            try:
                self.screen.blit(self.image, (0, 0))
            except:
                pass
                
        #Draw text tags:
        #self.screen.blit(self.text, self.textRect)
        if len(self.bin_text_tags) > 0:
            cnt = 0
            for i in range(self.ear.n_frequency_bins):
                if i == 0 or i == (self.ear.n_frequency_bins - 1):
                    continue
                if i % self.tag_every_n_bins == 0:
                    self.screen.blit(self.bin_text_tags[cnt], self.bin_rectangles[cnt])
                    cnt += 1
                    
        self.draw_text(self.screen, self.artist, self.fontLarge, self.TEXTCOLOR, (420,20))
        self.draw_text(self.screen, self.album, self.fontLarge, self.TEXTCOLOR, (420,120))
        self.draw_text(self.screen, self.title, self.fontLarge, self.TEXTCOLOR, (420,220))
        
        #text = self.fontLarge.render(self.metatxt, 1, self.TEXTCOLOR)
        #self.screen.blit(text, (550, 360))

        #self.history_button.draw(self.screen)
        #self.slow_bar_button.draw(self.screen)

        pygame.display.flip()


    def plot_bars(self):
        bars, slow_bars, new_slow_features = [], [], []
        local_height = self.y_ext[1] - self.y_ext[0]
        feature_values = self.frequency_bin_energies[::-1]

        for i in range(len(self.frequency_bin_energies)):
            feature_value = feature_values[i] * local_height

            self.fast_bars[i][3] = int(feature_value)

            if self.plot_audio_history:
                self.fast_bars[i][3] = int(feature_value + 0.02*self.HEIGHT)

            if self.add_slow_bars:
                self.decay = min(0.99, 1 - max(0,self.decay_speed * 60 / self.ear.fft_fps))
                slow_feature_value = max(self.slow_features[i]*self.decay, feature_value)
                new_slow_features.append(slow_feature_value)
                self.slow_bars[i][1] = int(self.fast_bars[i][1] + slow_feature_value)
                self.slow_bars[i][3] = int(self.slow_bar_thickness * local_height)

        if self.add_fast_bars:
            for i, fast_bar in enumerate(self.fast_bars):
                pygame.draw.rect(self.screen,self.fast_bar_colors[i],fast_bar,0)

        if self.plot_audio_history:
                self.prev_screen = self.screen.copy().convert_alpha()
                self.prev_screen = pygame.transform.rotate(self.prev_screen, 180)
                self.prev_screen.set_alpha(self.prev_screen.get_alpha()*self.alpha_multiplier)

        if self.add_slow_bars:
            for i, slow_bar in enumerate(self.slow_bars):
                pygame.draw.rect(self.screen,self.slow_bar_colors[i],slow_bar,0)

        self.slow_features = new_slow_features

        #Draw everything:
        self.screen.blit(pygame.transform.rotate(self.screen, 180), (0, 0))

