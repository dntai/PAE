from __future__ import division
import tensorflow as tf
import numpy as np
import platform
import matplotlib
if 'Linux' in platform.platform():
    matplotlib.use('Agg')
import matplotlib.pyplot as plt
from data import *
from setup import *
from generator import Generator
from discriminator import Discriminator
import time
import pickle
from tqdm import tqdm

class WGANGP:
    def __init__(self, data_dist, noise_dist, eps_dist, flags, args):
        self.epochs = flags.EPOCHS
        self.num_samples = flags.NUM_SAMPLES
        self.batch_size = flags.BATCH_SIZE
        self.learning_rate = flags.LEARNING_RATE
        self.data_dim = flags.DATA_DIM
        self.noise_dim = flags.NOISE_DIM
        self.gen_arch = flags.GEN_ARCH
        flags.DISC_ARCH[0] = flags.DATA_DIM
        self.disc_arch = flags.DISC_ARCH
        self.LAMBDA = 10
        self.n_critic = 5
        self.data_dist = data_dist
        self.noise_dist = noise_dist
        self.eps_dist = eps_dist
        self.db = flags.DATASET
        if args.db == 'cifar_100':
            self.skip = 75000
        else:
            self.skip = flags.SKIP
        # setup some alternative definitions given by user
        self.hidden_acti = args.hidden_acti
        self.disc_out_acti = args.disc_out_acti
        self.gen_out_acti = args.gen_out_acti
        self.working_dir = args.working_dir
        self.GPU = args.gpu
    
    def create_model(self):
        with tf.device('/device:GPU:'+str(self.GPU)):
            self.x = tf.placeholder(tf.float32, shape=(self.data_dim, None))
            self.z = tf.placeholder(tf.float32, shape=(self.noise_dim, None))
            self.eps = tf.placeholder(tf.float32, shape=(1, None))

            # Generator
            generator = Generator(self.gen_arch, self.hidden_acti, self.gen_out_acti, 'GEN')
            self.fake_data = generator(self.z)
            self.mixed_data = self.eps*self.x + (1.0 - self.eps)*self.fake_data

            # Discriminator
            discriminator = Discriminator(self.disc_arch, self.hidden_acti, self.disc_out_acti, 'DISC')
            self.true_output = discriminator(self.x)
            self.fake_output = discriminator(self.fake_data, reuse=True)
            self.mixed_output = discriminator(self.mixed_data, reuse=True)

            # Total cost
            grad = tf.gradients(self.mixed_output, self.mixed_data)[0]
            sqrt_norm = tf.sqrt(tf.reduce_sum(grad**2, axis=0))
            self.disc_cost = tf.reduce_mean(self.fake_output) - tf.reduce_mean(self.true_output) \
                        + self.LAMBDA*tf.reduce_mean((sqrt_norm - 1.0)**2)
            self.gen_cost = - tf.reduce_mean(self.fake_output)

            self.disc_params = tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES, 'DISC')
            self.gen_params = tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES, 'GEN')
            self.global_step = tf.Variable(0, trainable=False)
            self.disc_opt = tf.train.AdamOptimizer(self.learning_rate, beta1=0.0, beta2=0.9).minimize(
                self.disc_cost,
                var_list=self.disc_params
            )
            self.gen_opt = tf.train.AdamOptimizer(self.learning_rate, beta1=0.0, beta2=0.9).minimize(
                self.gen_cost,
                var_list=self.gen_params,
                global_step=self.global_step
            )

    def decode(self, z):
        return self.fake_data.eval(feed_dict={
            self.z:z
        })

    def load_model(self, sess, ckpt_id=None):
        saver = tf.train.Saver()
        path = os.path.join(self.working_dir, 'model')
        if ckpt_id:
            ckpt =  os.path.join(path,'saved-model-' + str(ckpt_id))
            saver.restore(sess, ckpt)
            print('\nLoaded %s\n'%ckpt)
        else:
            ckpt = tf.train.latest_checkpoint(path)
            print('\nFound latest model: %s'%ckpt)
            if ckpt:
                saver.restore(sess, ckpt)
                print('\nLoaded %s\n'%ckpt)

    def save_model(self, sess):
        saver = tf.train.Saver()
        path = os.path.join(self.working_dir,'model','saved-model')
        save_path = saver.save(sess, path, global_step=self.global_step.eval()+1)
        print('\nModel saved in %s'%save_path)
    
    def save_log(self,log):
        path = os.path.join(self.working_dir,'model','log.pkl')
        with open(path,'wb') as f:
            pickle.dump(log,f)

    def load_log(self,log):
        path = os.path.join(self.working_dir,'model','log.pkl')
        if os.path.exists(path):
            with open(path,'rb') as f:
                data = pickle.load(f)
                log['disc_costs'] = data['disc_costs']
                log['gen_costs'] = data['gen_costs']
                log['train_time'] = data['train_time']

    def train(self):
        true_data,_ = self.data_dist.sample(self.batch_size)

        init = tf.global_variables_initializer()
        config=tf.ConfigProto(allow_soft_placement=True, log_device_placement=True)
        config.gpu_options.allow_growth = True
        log = {'disc_costs':[],'gen_costs':[],'train_time':0}
        start = time.time()
        with tf.Session(config=config) as sess:
            sess.run(init)

            # model loading
            self.load_model(sess)
            self.load_log(log)

            # how many iters?
            n_iters = int(self.num_samples/self.batch_size)*self.epochs
            n_iters_left = n_iters - self.global_step.eval()
            print('\nNumber of train iterations %d'%n_iters_left)
            
            for t in tqdm(range(n_iters_left)):
                if self.db not in ['grid','ring','low_dim_embed']:
                    true_data,_ = self.data_dist.sample(self.batch_size)
                for j in range(self.n_critic):
                    noise = self.noise_dist.sample(self.batch_size)
                    eps = self.eps_dist.sample(self.batch_size)
                    _ = sess.run(self.disc_opt, feed_dict={
                        self.z: noise,
                        self.x: true_data,
                        self.eps: eps
                    })
                noise = self.noise_dist.sample(self.batch_size)
                _ = sess.run(self.gen_opt, feed_dict={
                    self.z: noise
                })

                disc_cost, gen_cost = sess.run([self.disc_cost, self.gen_cost], feed_dict={
                    self.x: true_data,
                    self.z: noise,
                    self.eps: eps
                })

                if self.global_step.eval() % 100 == 0: # saving at every iteration is not neccessary
                    log['disc_costs'].append(disc_cost)
                    log['gen_costs'].append(gen_cost)
                    log['train_time'] += (time.time()-start)
                    start = time.time()

                if self.global_step.eval() % self.skip == 0: 
                    # plotting
                    fig = plt.figure(1,figsize=(15,5))
                    fig.add_subplot(1,2,1)
                    plt.plot(log['disc_costs'])
                    plt.title('Discriminator cost')
                    plt.xlabel('Iterations/100')
                    fig.add_subplot(1,2,2)
                    plt.plot(log['gen_costs'])
                    plt.title('Generator cost')
                    plt.xlabel('Iterations/100')
                    path = os.path.join(self.working_dir,'figure')
                    plt.savefig(os.path.join(path,'train-'+str(self.global_step.eval()+1)+'.png'),bbox_inches='tight',dpi=800)
                    plt.close()
                    # save model & log
                    self.save_log(log)
                    self.save_model(sess)
                
def run(args):
    DATASET = args.db
    flags = SETUP(DATASET)
    flags.LEARNING_RATE = 1e-4
    if DATASET == 'grid':
        data_dist = Grid()
    elif DATASET == 'low_dim_embed':
        data_dist = LowDimEmbed()
    elif DATASET == 'color_mnist':
        data_dist = CMNIST(os.path.join('data','mnist'))
    elif DATASET == 'cifar_100':
        data_dist = CIFAR100(os.path.join('data','cifar-100'))
        
    noise_dist = NormalNoise(flags.NOISE_DIM)
    eps_dist = UniformNoise(1)
    model = WGANGP(data_dist, noise_dist, eps_dist, flags, args)
    model.create_model()
    model.train()
